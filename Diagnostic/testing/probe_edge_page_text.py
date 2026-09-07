"""What the sign-in window is actually asking for, in words.

WHY
    The step that stalls the automatic sign-in is a Microsoft page, and from the
    outside every Microsoft page looks the same: the tab is on
    login.microsoftonline.com and nothing moves. This prints the words on that
    page - the account names it offers, or the prompt it is waiting on - so the
    stall can be answered instead of guessed at.

    Nothing is typed or clicked here, and no cookie value is printed.

    python probe_edge_page_text.py [seconds-before-reading]
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import edge_cdp  # noqa: E402

SSO_START = ("https://elieric.okbase.cz/okbase/web-client/web?sso=yes"
             "&dataSource=defaultDataSource&organization=1")


def page_socket(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5.0) as r:
        tabs = json.loads(r.read().decode("utf-8", "replace"))
    for tab in tabs:
        if tab.get("type") == "page":
            return str(tab.get("webSocketDebuggerUrl") or "")
    return ""


def read_text(port: int) -> tuple[str, str]:
    url = page_socket(port)
    if not url:
        return "", "", ""
    ws = edge_cdp._Ws(url)
    try:
        where, _ = ws.call("Runtime.evaluate",
                           {"expression": "location.href", "returnByValue": True})
        text, err = ws.call(
            "Runtime.evaluate",
            {"expression": "document.body ? document.body.innerText : ''",
             "returnByValue": True})
        return (str((where.get("result") or {}).get("value") or ""),
                str((text.get("result") or {}).get("value") or ""), err)
    finally:
        ws.close()


def main(argv: list[str]) -> int:
    delay = float(argv[1]) if len(argv) > 1 else 8.0
    port = edge_cdp._free_port()
    proc, err = edge_cdp.launch(edge_cdp.MENU_PAGE, port)
    if proc is None:
        print(f"launch failed: {err}")
        return 1
    try:
        url, err = edge_cdp.browser_socket_url(port)
        if not url:
            print(f"no debug port: {err}")
            return 1
        time.sleep(2.5)
        ws = edge_cdp._Ws(page_socket(port))
        ws.call("Page.navigate", {"url": SSO_START})
        ws.close()
        time.sleep(delay)
        where, text, err = read_text(port)
        print(f"url : {where[:160]}")
        if err:
            print(f"error: {err}")
        print("---- what the page says ----")
        print(text[:2000])
        return 0
    finally:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main(sys.argv))
