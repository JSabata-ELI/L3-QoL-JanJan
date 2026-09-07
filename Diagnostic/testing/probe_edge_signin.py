"""Watch the Edge sign-in the Settings button uses, step by step, with times.

WHY
    "Sign in with Edge did nothing" is impossible to tell apart from the outside.
    The button shows one sentence at a time and the several ways it can stall all
    look the same to a person: Edge not found, the window handed itself to an
    Edge that was already running and quit, the debug port never opened, the
    portal's own sign-in page, Microsoft asking which account. This prints each
    step with the second it happened on, so the slow or stuck one names itself.

    It uses the module's own walk - the same code the button runs - so there is
    nothing here to drift out of step with it.

    python probe_edge_signin.py [account] [seconds]

Nothing here prints a cookie value.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import edge_cdp  # noqa: E402
import okbase_menu as om  # noqa: E402


def main(argv: list[str]) -> int:
    account = argv[1] if len(argv) > 1 else ""
    wait_s = float(argv[2]) if len(argv) > 2 else 120.0
    t0 = time.monotonic()

    def stamp(text: str) -> None:
        print(f"[{time.monotonic() - t0:5.1f}s] {text}")

    exe, err = edge_cdp.find_edge()
    stamp(f"Edge     : {exe or 'NOT FOUND: ' + err}")
    if not exe:
        return 1
    stamp(f"profile  : {edge_cdp.profile_dir()}")
    stamp(f"page     : {edge_cdp.MENU_PAGE}")
    stamp(f"sso start: {edge_cdp.sso_start_url()}")
    stamp(f"account  : {account or '(none - the window will wait for you)'}")

    line, err = edge_cdp.renew(account=account, timeout_s=wait_s, progress=stamp)
    if not line:
        stamp(f"FAILED: {err}")
        return 1
    if err:
        stamp(f"WARNING: {err}")
    for row in om.describe_cookies(om.parse_cookies(line)):
        print("   " + row)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
