"""Describe a captured 'save my lunch order' request without leaking the sign-in.

The ordering page is the same Angular front end over the same JSON API as the
menu, so ticking a checkbox and pressing "save changes" is one HTTP request.
This script turns a "Copy as cURL" of that request into something safe to paste
into a chat: the URL, the method, the OKbase headers and the JSON body, with
every cookie reduced to its name and length.

Usage:
    python probe_order_curl.py <file with the copied cURL>
    python probe_order_curl.py --clipboard
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import okbase_menu as om

_SECRET = ("cookie", "authorization", "x-xsrf-token", "x-csrf-token")


def _read_clipboard() -> str:
    try:
        import subprocess

        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            capture_output=True, text=True, timeout=20,
        )
        return out.stdout or ""
    except Exception as exc:                        # noqa: BLE001
        print(f"could not read the clipboard: {exc}")
        return ""


def describe(text: str) -> None:
    url, method, headers, body = om.parse_curl(text)
    if not url:
        print("This does not look like a copied cURL command.")
        return

    print(f"method  : {method or 'GET'}")
    print(f"url     : {url}")
    print("")
    print("headers (secrets shown by name only):")
    for name, value in sorted(headers.items()):
        if name.lower() in _SECRET:
            if name.lower() == "cookie":
                for line in om.describe_cookies(om.parse_cookies(value)):
                    print(f"  {line}")
            else:
                print(f"  {name}: <{len(value)} characters, hidden>")
        else:
            print(f"  {name}: {value}")

    print("")
    if not body:
        print("body    : none")
        return
    try:
        parsed = json.loads(body)
    except Exception:                               # noqa: BLE001
        print("body (not JSON):")
        print(body[:4000])
        return
    print("body:")
    print(json.dumps(parsed, indent=2, ensure_ascii=False)[:8000])


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    if argv[0] in ("--clipboard", "-c"):
        text = _read_clipboard()
    else:
        text = Path(argv[0]).read_text(encoding="utf-8", errors="replace")
    describe(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
