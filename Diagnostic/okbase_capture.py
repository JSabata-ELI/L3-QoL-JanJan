"""Set the canteen menu up from a request copied out of the browser.

Run this when `/food` reports that the OKbase sign-in has expired, and once at
the very beginning. It is the only manual step the feature ever needs.

WHY IT EXISTS
    OKbase is reached through Microsoft single sign-on, usually with a
    confirmation in the authenticator. No program can pass that prompt — that is
    what it is for — so instead the program borrows a sign-in a person has
    already completed in the browser. What is pasted in carries the company
    sign-on (`_shibsession_…`) as well as the session itself, which is why the
    program can afterwards renew a lapsed session on its own for a long time
    (see okbase_menu.REVIVE_PATHS). This script only has to be run again when
    that company sign-on finally lapses.

THE NORMAL WAY IS NO LONGER THIS SCRIPT
    Settings → Canteen menu now has two buttons — **Sign in with Edge** and
    **Paste sign-in from clipboard** — which do everything below without a shell.
    Use those. This script is the fallback for when the app will not start, and
    for checking things by hand.

WHAT TO DO
    Easiest:  python okbase_capture.py --edge
    A browser window opens on the OKbase page; sign in there and it takes the
    sign-in out of that window itself. Nothing to copy.

    Or, by hand:
    1. In the browser, signed in to OKbase, press F12 and open Network. If the
       tab is not there, click "+" and pick it.
    2. Open Stravování → Objednávka jídel so the list fills up, then right-click
       the row called  nacti-vse  → Copy → "Copy as cURL".
    3. Run:   python okbase_capture.py

    Diagnostic does NOT have to be closed first: the settings file is merged
    rather than rewritten, and a running app picks a new sign-in up by itself
    (okbase_menu.save_user_settings, monitor_tab._reload_okbase_sign_in).

It reads the copied command straight from the clipboard, so nothing has to be
pasted anywhere else. Out of it come four things, all saved into the Windows account's own settings
file (`%APPDATA%\\Diagnostic\\okbase.json`) exactly as the Settings dialog would
save them — which is why a rebuilt Diagnostic inherits the sign-in instead of
arriving without one:

    okbase_session_cookie   the sign-in, DPAPI-encrypted for this Windows
                            account only, and never written to the share
    okbase_user_id          the portal wants it inside the query
    okbase_canteen_id       which canteen
    okbase_filter           the request itself, kept as a template — its dates
                            are re-pointed at the current week on every read

Then it reads the menu straight away and prints it, so it is obvious at once
whether it worked, and saves that menu where the bot looks for it.

Cookie VALUES are never printed — only the cookie names and their lengths — so a
live sign-in cannot end up in a screenshot or a chat window.

Options:
    --edge          open a browser window and take the sign-in from it
    --file <path>   read the copied command from a file instead of the clipboard
    --dry           show what it found and test it, but change nothing

Exit code 2 means the PORTAL did not answer — which says nothing about the
sign-in, so do not go and copy a new one on the strength of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import okbase_menu as om  # noqa: E402
from secrets_util import encrypt_secret  # noqa: E402


# --------------------------------------------------------------------------- #
# Getting the copied command
# --------------------------------------------------------------------------- #

def read_clipboard() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True, text=True, timeout=30)
        return out.stdout or ""
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read the clipboard: {exc}")
        return ""


# parse_curl / cookie_of / read_paste now live in okbase_menu, so that this
# script, the Settings dialog and parse_cookies all read a paste identically.
parse_curl = om.parse_curl
cookie_of = om.cookie_of


def describe_cookies(jar: dict) -> None:
    """Names and lengths only — never the values.

    The wording lives in okbase_menu.describe_cookies so that this script, the
    Settings dialog and `okbase_menu.py --check` all say the same thing. Only the
    printing is here.
    """
    print("")
    for line in om.describe_cookies(jar):
        print(line)


# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    args = argv[1:]
    dry = "--dry" in args
    source = ""
    if "--edge" not in args:
        if "--file" in args:
            try:
                source = Path(args[args.index("--file") + 1]).read_text(
                    encoding="utf-8")
            except (IndexError, OSError) as exc:
                print(f"Could not read that file: {exc}")
                return 1
        else:
            source = read_clipboard()

    if "--edge" in args:
        # No copying at all: open a browser window and take the sign-in out of
        # it. The same thing the Settings button does — this is here for when
        # the app will not start.
        import edge_cdp
        # The saved Work account, so this route answers Microsoft's "Pick an
        # account" for itself exactly as the button does. Without it the window
        # would stop on that question — and this is the route used when the app
        # will not start, which is the worst moment to have to know that.
        account = str(om.load_user_settings().get("okbase_account") or "")
        line, problem = edge_cdp.renew(account=account,
                                       progress=lambda t: print(f"  {t}"))
        if not line:
            print(f"The browser sign-in did not come through: {problem}")
            return 1
        if problem:
            print(f"WARNING: {problem}")
        got = om.PastedSignIn(cookies=line, jar=om.parse_cookies(line),
                              source="a browser window")
    else:
        got = om.read_paste(source)
    if got.problem:
        print(got.problem)
        return 1

    print(f"read    : {got.source}")
    print(f"address : {got.base or om.BASE_DEFAULT}")
    describe_cookies(got.jar)

    base = got.base or om.BASE_DEFAULT
    print(f"\nchecking the sign-in against {base} …")
    session, err = om.session_from_cookie(got.cookies, base)
    if session is None:
        if om.is_unreachable(err):
            print(f"  the PORTAL did not answer: {err}")
            print("  This says nothing about the sign-in. Try again shortly; do")
            print("  not go and copy anything yet.")
            return 2
        print(f"  FAILED: {err}")
        print("  Sign in to OKbase again in the browser, copy the request "
              "afresh, and re-run this straight away.")
        return 1
    print("  the sign-in works.")

    filter_body = got.filter_body
    user_id = got.user_id
    canteen_id = got.canteen_id
    if user_id:
        print(f"  the query also carries userId {user_id}")

    monday = om.week_monday(date.today())
    days, used, err = om.fetch_weeks(session, monday, om.FETCH_WEEKS, base,
                                     known_filter=filter_body, user_id=user_id)
    if not days:
        print(f"\nThe menu could not be read: {err}")
        return 1

    print(f"\nread {len(days)} day(s). The request the portal accepted:")
    print(f"  {json.dumps(used, ensure_ascii=False)}")
    today = days.get(date.today().isoformat()) or []
    print("\ntoday:")
    for meal in today:
        print(f"  {meal.name} — {meal.price}  [{meal.kind}]")
    if not today:
        print("  (nothing on the menu for today)")

    if dry:
        print("\n--dry: nothing was saved.")
        return 0

    # --- save it exactly as the Settings dialog would ---------------------
    # Into the Windows account's own file, not next to the program: the app has
    # several homes (source, and every version under C:\Dev\dist) and they all
    # read this one. That is what makes a rebuild inherit the sign-in instead of
    # arriving without it. See okbase_menu.OKBASE_KEYS.
    settings = {
        "okbase_enabled": True,
        "okbase_session_cookie": encrypt_secret(cookie_of(headers)),
    }
    if base != om.BASE_DEFAULT:
        settings["okbase_base_url"] = base
    if isinstance(used, dict):
        settings["okbase_filter"] = used
    if user_id:
        settings["okbase_user_id"] = user_id
    if canteen_id:
        settings["okbase_canteen_id"] = canteen_id

    problem = om.save_user_settings(settings)
    if problem:
        print(f"\nCould not save the settings: {problem}")
        return 1
    print(f"\nsaved into {om.user_settings_path()}")
    print("  okbase_enabled        = true")
    print("  okbase_session_cookie = encrypted for this Windows account")
    if user_id:
        print(f"  okbase_user_id        = {user_id}")
    if canteen_id:
        print(f"  okbase_canteen_id     = {canteen_id}")
    print(f"  okbase_filter         = {json.dumps(used, ensure_ascii=False)}")

    # The menu too, so /food answers before the app is even opened.
    cache = om.build_cache(days, datetime.now(), used)
    paths = [om.local_cache_path()]
    shared = om.shared_cache_path()
    if shared is not None:
        paths.append(shared)
    problem = om.write_cache(cache, paths)
    for path in paths:
        print(f"menu saved to {path}")
    if problem:
        print(f"  (one copy failed: {problem})")

    print("\nDone. Open Diagnostic and ask the bot /food.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
