"""Borrow an OKbase sign-in from a real Edge window, so nobody has to copy it.

WHY THIS EXISTS
    The canteen menu comes from a portal behind Microsoft single sign-on with a
    confirmation in the authenticator. No program can pass that prompt — that is
    what it is for — so `okbase_menu.session_from_cookie` borrows a sign-in a
    person has already completed. Until now the borrowing was done by hand: F12,
    Network, right-click the request, Copy as cURL, paste. That is a five-step
    expedition for a lunch menu, and it had to be redone every time the sign-in
    finally lapsed.

    This module does the same borrowing with one button. It opens a **visible**
    Edge window on the OKbase page, lets the person sign in exactly as they would
    anyway, and then reads the cookies out of that window. What it hands back is
    a plain `Cookie:` line — the same text the Settings field has always taken —
    so nothing downstream changes at all.

WHY EDGE, AND WHY A WINDOW OF ITS OWN
    Edge on a domain-joined PC can sign in to the work account off the machine's
    own Windows sign-in, so in the good case the person clicks the button, sees
    their account, and is done — no authenticator, no typing. A bundled Chromium
    (Qt WebEngine) cannot do that, and would have added about 240 MB to the
    shared support folder that sits next to every program in this repo.

    The window uses a profile of its own under LOCALAPPDATA, and that is not a
    compromise but a requirement: since Chrome/Edge 136, `--remote-debugging-port`
    is IGNORED unless it is paired with a non-default `--user-data-dir`. It is
    also what keeps this well clear of the operator's real Edge, which is never
    touched, never restarted and never closed.

    Deliberately LOCALAPPDATA and not the `%APPDATA%\\Diagnostic` this program
    keeps its settings in: a browser profile is a cache of hundreds of megabytes
    and must not roam with the Windows profile.

ONE THING TO KNOW
    While that window is open the debug port will hand its cookies to anything
    running as this Windows account. So the port is picked at random rather than
    fixed, and the window is closed the moment the sign-on cookie appears —
    seconds, not hours. Nothing here ever prints a cookie value.

Nothing in here raises. Every function that touches the browser or the network
returns a value plus an error string, the same discipline okbase_menu follows.

Shell use, for checking it by hand:

    python edge_cdp.py                        sign in, print the cookie NAMES
    python edge_cdp.py you@example.com        …picking that account when asked
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import okbase_menu as om  # noqa: E402  (the portal's addresses have one owner)

# The page to land on. okbase_menu owns every portal address, so this is a
# reference and not a second copy to drift out of step.
MENU_PAGE = om.MENU_PAGE_DEFAULT

# How long to wait for the human. A phone is slow to reach and an authenticator
# prompt can sit unnoticed for a while, so this is generous on purpose.
SIGN_IN_TIMEOUT_S = 300.0
# How long Edge gets to open its debug port. Cold, on a busy PC, it is not quick.
PORT_TIMEOUT_S = 30.0
POLL_S = 1.0

EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

# The cookie that matters. The session id is disposable — the portal mints a new
# one from this whenever it needs to (okbase_menu.REVIVE_PATHS).
SSO_COOKIE_PREFIX = "_shibsession_"

# The two pages this has to get PAST, and how.
#
# Landing on the menu page is not enough. The portal bounces a caller with no
# session to its own sign-in page, and that page is a dead end for a program: it
# shows a form nobody here can fill in, plus a "company account" button that has
# to be clicked. The button's target is a plain URL, so the window is walked
# straight there instead — no click, and it is the portal's own route, not a
# guess (it is the one the browser follows when a person clicks it).
LOGIN_PAGE_MARK = "web-client/login"
SSO_START_QUERY = "?sso=yes&dataSource=defaultDataSource&organization=1"

# And then Microsoft asks WHICH account. On a work PC with more than one work
# account signed in — the usual case here — that question is always asked, and
# the wrong answer signs in to a tenant the portal knows nothing about. Given
# the address to look for, the tile is clicked automatically; without it, the
# person is told what the window is waiting for.
MS_LOGIN_HOST = "login.microsoftonline.com"

# A second Edge on the same profile is not a second browser: Windows gives the
# address to the one already running and the new process exits at once. Said in
# these words because it is repaired automatically and the operator only ever
# sees it in a log.
HANDED_OFF = "handed to a browser that was already open on this profile"


def sso_start_url(page: str = MENU_PAGE) -> str:
    """The address that STARTS the company sign-on for `page`'s instance."""
    root = page.split("/okbase/", 1)[0]
    return f"{root}/okbase/web-client/web{SSO_START_QUERY}"


# --------------------------------------------------------------------------- #
# Where things are
# --------------------------------------------------------------------------- #

def find_edge() -> tuple[str, str]:
    """(path, "") to msedge.exe, or ("", reason)."""
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(
                        root,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                        r"\App Paths\msedge.exe") as key:
                    path = winreg.QueryValue(key, None)
                if path and os.path.isfile(path):
                    return path, ""
            except OSError:
                continue
    except Exception:  # noqa: BLE001 - the fixed paths below are the fallback
        pass
    for path in EDGE_PATHS:
        if os.path.isfile(path):
            return path, ""
    return "", "Microsoft Edge could not be found on this PC"


def profile_dir() -> Path:
    """The browser profile this feature keeps to itself."""
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "Diagnostic" / "edge-profile"


def _free_port() -> int:
    """A port nobody is using. Never a fixed 9222 — a developer may have it."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def profile_edge_pids(folder=None) -> list[int]:
    """The browsers already running on this feature's own profile folder.

    Windows has to be asked, because such a browser is invisible from in here
    and it is the whole reason a fresh launch can end two seconds after it
    starts: a second Edge on the same profile does not start a browser at all,
    it hands its address to the one already running and quits (`renew`).
    """
    folder = str(folder or profile_dir()).replace("'", "''")
    query = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{folder}*' }} | "
             "Select-Object -ExpandProperty ProcessId")
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", query],
            capture_output=True, text=True, timeout=20,
            creationflags=_no_window())
    except Exception:  # noqa: BLE001 - not knowing is not worth an exception
        return []
    return [int(word) for word in done.stdout.split() if word.isdigit()]


def close_profile_edges(folder=None) -> int:
    """Close any leftover browser on our own profile. Returns how many.

    Safe by construction: this profile belongs to this one feature, so nothing
    the operator is using can be closed here — their own Edge, with their own
    tabs, runs on a different profile and is not touched.
    """
    pids = profile_edge_pids(folder)
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15,
                           creationflags=_no_window())
        except Exception:  # noqa: BLE001
            pass
    return len(pids)


# --------------------------------------------------------------------------- #
# A websocket client, because the stdlib has none
# --------------------------------------------------------------------------- #
#
# Reading cookies is one DevTools command, and DevTools commands travel over a
# websocket. Rather than carry a whole library into the shared bundle for one
# request, this is the client: a handshake, masked frames out, plain frames in.
# It is deliberately the smallest thing that is CORRECT — fragmented messages and
# pings are handled, because a cookie list is big enough to arrive in pieces and
# Chromium does ping.

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _Ws:
    """One websocket connection to the browser. Never raises out of `call`."""

    def __init__(self, url: str, timeout: float = 20.0):
        rest = url.split("://", 1)[-1]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self._sock = socket.create_connection((host, int(port or 80)), timeout)
        self._sock.settimeout(timeout)
        self._buf = b""
        self._next_id = 0
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET /{path} HTTP/1.1\r\n"
            f"Host: {hostport}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n")
        self._sock.sendall(request.encode("ascii"))
        head = self._read_until(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("ascii", "replace")
        if " 101 " not in status:
            raise OSError(f"the browser refused the debug connection: "
                          f"{status[:80]}")

    # --- plumbing ------------------------------------------------------
    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise OSError("the browser closed the debug connection")
            self._buf += chunk
        head, _, self._buf = self._buf.partition(marker)
        return head + marker

    def _read_exactly(self, count: int) -> bytes:
        while len(self._buf) < count:
            chunk = self._sock.recv(max(65536, count - len(self._buf)))
            if not chunk:
                raise OSError("the browser closed the debug connection")
            self._buf += chunk
        out, self._buf = self._buf[:count], self._buf[count:]
        return out

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = secrets.token_bytes(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _read_message(self) -> str:
        """One whole text message, reassembled across fragments."""
        parts: list[bytes] = []
        while True:
            byte0, byte1 = self._read_exactly(2)
            fin = bool(byte0 & 0x80)
            opcode = byte0 & 0x0F
            masked = bool(byte1 & 0x80)
            length = byte1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exactly(8))[0]
            key = self._read_exactly(4) if masked else b""
            data = self._read_exactly(length) if length else b""
            if key:
                data = bytes(b ^ key[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:
                raise OSError("the browser closed the debug connection")
            if opcode == 0x9:                      # ping -> pong, keep waiting
                self._send_frame(data, opcode=0xA)
                continue
            if opcode == 0xA:                      # a pong of our own; ignore
                continue
            parts.append(data)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    # --- the one thing callers want ------------------------------------
    def call(self, method: str, params: dict | None = None,
             timeout: float = 20.0) -> tuple[dict, str]:
        """Send one DevTools command, wait for ITS answer. (result, error)."""
        self._next_id += 1
        want = self._next_id
        try:
            self._send_frame(json.dumps(
                {"id": want, "method": method,
                 "params": params or {}}).encode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {}, f"could not ask the browser: {exc}"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = json.loads(self._read_message())
            except OSError as exc:
                return {}, str(exc)
            except ValueError:
                continue                            # not JSON; ignore it
            # Events arrive on the same socket and are not the answer.
            if message.get("id") != want:
                continue
            if "error" in message:
                detail = (message["error"] or {}).get("message") or "refused"
                return {}, f"{method}: {detail}"
            return message.get("result") or {}, ""
        return {}, f"{method}: the browser did not answer in time"

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=0x8)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #
# Talking to the browser
# --------------------------------------------------------------------------- #

def browser_is_up(port: int) -> bool:
    """Is a browser still listening on this debug port?

    The one honest test for "is the window still there". A process id is NOT:
    Edge is entitled to hand its own work to a fresh process and leave the port
    open, and the pid that was started is then gone while the window is not.
    """
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2.0):
            return True
    except Exception:  # noqa: BLE001
        return False


def browser_socket_url(port: int, wait_s: float = PORT_TIMEOUT_S,
                       proc=None) -> tuple[str, str]:
    """The browser's own debug address, once it is listening. (url, error).

    `proc` is the process that was started, if there is one. It is watched only
    to end the wait early: a process that quits without ever listening was a
    hand-off to a browser already running on this profile, and waiting out the
    full half minute for a port that will never open only turns a fixable
    situation into a mysterious one.
    """
    deadline = time.monotonic() + wait_s
    last = "the browser did not open its debug port"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=2.0) as r:
                info = json.loads(r.read().decode("utf-8", "replace"))
            url = str(info.get("webSocketDebuggerUrl") or "")
            if url:
                return url, ""
            last = "the browser answered without a debug address"
        except Exception as exc:  # noqa: BLE001 - it is simply not up yet
            last = f"{exc}"
        if proc is not None and proc.poll() is not None:
            return "", HANDED_OFF
        time.sleep(POLL_S)
    return "", last


# Two spellings of the same question. `Storage.getCookies` is the one the browser
# target answers; `Network.getAllCookies` is the older name and is kept as a
# fallback, the same "try the plausible shapes and keep what answers" discipline
# okbase_menu uses for the portal's own undocumented request body.
COOKIE_METHODS = ("Storage.getCookies", "Network.getAllCookies")


def all_cookies(ws: _Ws) -> tuple[list[dict], str]:
    """Every cookie the browser holds. (cookies, error)."""
    last = "the browser would not list its cookies"
    for method in COOKIE_METHODS:
        result, err = ws.call(method)
        if not err:
            return list(result.get("cookies") or []), ""
        last = err
    return [], last


def cookies_for(cookies: list[dict], host: str) -> dict[str, str]:
    """The cookies belonging to one host, as {name: value}.

    Filtered by domain on purpose: the profile will also hold Microsoft's own
    sign-on cookies, and those are neither wanted nor ours to pass around.
    """
    host = (host or "").lower().lstrip(".")
    out: dict[str, str] = {}
    for c in cookies:
        domain = str(c.get("domain") or "").lower().lstrip(".")
        if not domain or not (host == domain or host.endswith("." + domain)):
            continue
        name = str(c.get("name") or "")
        if name:
            out[name] = str(c.get("value") or "")
    return out


def cookie_line(jar: dict[str, str]) -> str:
    """`a=1; b=2` — deliberately the exact text okbase_menu.parse_cookies eats,
    which is why nothing downstream has to know this module exists."""
    return "; ".join(f"{name}={value}" for name, value in jar.items())


# --------------------------------------------------------------------------- #
# The tab, as opposed to the browser
# --------------------------------------------------------------------------- #
#
# Cookies are a browser-wide question and are asked on the browser's own socket.
# Everything below is about the one open TAB — where it is, moving it, clicking
# in it — and the browser socket does not answer those: a page command needs the
# page's own socket. Hence a second, short-lived connection per command, which
# for one navigation every few seconds costs nothing.

def page_url(ws: _Ws) -> str:
    """Where the open tab is, or "". Asked on the BROWSER socket."""
    result, err = ws.call("Target.getTargets", timeout=5.0)
    if err:
        return ""
    for info in result.get("targetInfos") or []:
        if info.get("type") == "page":
            return str(info.get("url") or "")
    return ""


def _page_socket(port: int) -> str:
    """The open tab's own debug address, or ""."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json",
                                    timeout=5.0) as r:
            tabs = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return ""
    for tab in tabs:
        if tab.get("type") == "page":
            return str(tab.get("webSocketDebuggerUrl") or "")
    return ""


def _in_page(port: int, method: str, params: dict) -> tuple[dict, str]:
    """One DevTools command on the open tab. (result, error). Never raises."""
    url = _page_socket(port)
    if not url:
        return {}, "the browser has no page open"
    ws = None
    try:
        ws = _Ws(url)
        return ws.call(method, params)
    except Exception as exc:  # noqa: BLE001
        return {}, f"{exc}"
    finally:
        if ws is not None:
            ws.close()


def navigate(port: int, url: str) -> str:
    """Send the open tab to `url`, as clicking a link would. "" when done."""
    _, err = _in_page(port, "Page.navigate", {"url": url})
    return err


def bring_to_front(port: int) -> str:
    """Put the window where a person can see it.

    The Settings dialog that starts this is modal, and a new window can come up
    behind it — which is exactly what "the button did nothing" looks like from
    the outside while the sign-in sits waiting on a page nobody can see.
    """
    _, err = _in_page(port, "Page.bringToFront", {})
    return err


# The account picker, in the page's own terms. Written to click the tile that
# CARRIES the address and nothing else: the tightest element whose text contains
# it, which on Microsoft's picker is the address line inside the row. A click
# there rises to the row's own handler, so the row does not have to be found.
_PICK_ACCOUNT_JS = r"""
(function (want) {
  want = String(want || '').trim().toLowerCase();
  if (!want) return 'no-account-given';
  var body = document.body ? (document.body.innerText || '') : '';
  if (body.toLowerCase().indexOf(want) < 0) return 'not-offered';
  var all = document.querySelectorAll('div,a,li,button,span,td');
  var best = null, bestLen = 1e9;
  for (var i = 0; i < all.length; i++) {
    var text = (all[i].innerText || '').trim().toLowerCase();
    if (text.indexOf(want) < 0) continue;
    if (text.length < bestLen) { best = all[i]; bestLen = text.length; }
  }
  if (!best) return 'not-offered';
  var el = best;
  for (var n = 0; n < 6 && el && el.tagName !== 'BODY'; n++) {
    var role = el.getAttribute ? el.getAttribute('role') : null;
    if (role === 'button' || el.tagName === 'A' || el.tagName === 'BUTTON') break;
    el = el.parentElement;
  }
  if (!el || el.tagName === 'BODY') el = best;
  el.click();
  return 'clicked';
})(%s)
"""


def pick_account(port: int, account: str) -> tuple[str, str]:
    """Click the tile for `account` on Microsoft's "Pick an account" page.

    Returns (verdict, error) where verdict is 'clicked', 'not-offered' (that
    address is not on this page — so it is some other Microsoft page, and the
    person has to deal with it) or 'no-account-given'.
    """
    result, err = _in_page(port, "Runtime.evaluate", {
        "expression": _PICK_ACCOUNT_JS % json.dumps(account),
        "returnByValue": True})
    if err:
        return "", err
    return str((result.get("result") or {}).get("value") or ""), ""


# --------------------------------------------------------------------------- #
# The whole job
# --------------------------------------------------------------------------- #

def launch_args(exe: str, folder, port: int, url: str) -> list[str]:
    """Exactly how the window is opened. Its own function so it can be checked.

    Two of these flags are load-bearing and easy to lose:

    * `--user-data-dir` — since Chrome/Edge 136 the debug port is IGNORED
      without a non-default one. It is also what keeps this out of the
      operator's real browser.
    * no headless flag, ever. The visible window IS the feature: a person has to
      be able to complete a Microsoft prompt in it.
    """
    return [
        exe,
        f"--user-data-dir={folder}",
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        url,
    ]


def launch(url: str, port: int) -> tuple[subprocess.Popen | None, str]:
    """Open the sign-in window. (process, error)."""
    exe, err = find_edge()
    if not exe:
        return None, err
    folder = profile_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return None, f"could not make the browser profile folder: {exc}"
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(launch_args(exe, folder, port, url),
                                creationflags=flags), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"could not start Edge: {exc}"


def _site(url: str) -> str:
    """The bit of an address a person reads — the site it is on."""
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return host or url[:60]


def renew(host: str = "", page: str = MENU_PAGE,
          timeout_s: float = SIGN_IN_TIMEOUT_S,
          progress=None, account: str = "") -> tuple[str, str]:
    """Open a window, wait for the sign-in, hand back a Cookie line.

    Returns (cookie_line, error). `progress` is an optional callable taking one
    sentence, so a dialog can say what is happening while a person is signing in.

    `host` defaults to the host of `page`. `account` is the work address to pick
    when Microsoft asks which one — with two work accounts on a PC it always
    asks, and only one of them is the one the portal knows.

    It does as much of the walk as a program is allowed to: the portal's own
    sign-in page is stepped over and the account is picked. Everything after
    that — a password, an authenticator prompt — is the person's, in a window
    that is deliberately visible and in front.
    """
    def say(text: str) -> None:
        if progress is not None:
            try:
                progress(text)
            except Exception:  # noqa: BLE001 - never let the caller break this
                pass

    host = host or page.split("//", 1)[-1].split("/", 1)[0]
    port = _free_port()
    say("opening a browser window…")
    proc, err = launch(page, port)
    if proc is None:
        return "", err

    ws = None
    try:
        url, err = browser_socket_url(port, proc=proc)
        if not url and err == HANDED_OFF:
            # A browser left over from an earlier attempt swallowed the address
            # and no window of ours ever opened. Clear it and start again once —
            # this profile is nobody else's, so there is nothing to lose by it.
            say("a browser was still open from before — closing it…")
            close_profile_edges()
            port = _free_port()
            proc, err = launch(page, port)
            if proc is None:
                return "", err
            say("opening a browser window…")
            url, err = browser_socket_url(port, proc=proc)
        if not url:
            return "", f"could not talk to the browser ({err})"
        try:
            ws = _Ws(url)
        except Exception as exc:  # noqa: BLE001
            return "", f"could not talk to the browser ({exc})"

        bring_to_front(port)
        say("waiting for the Microsoft sign-in…")
        deadline = time.monotonic() + timeout_s
        best: dict[str, str] = {}
        pushed = ""            # the login page already stepped over
        picked = ""            # the Microsoft page the account was picked on
        told = ""              # the page last named out loud, so each is said once
        closed = False         # the window went away before the sign-in finished
        while time.monotonic() < deadline:
            cookies, err = all_cookies(ws)
            if err:
                # The browser stopped answering. Whether that is a closed window
                # is asked of the PORT and never of the process id: Edge may put
                # its work into a fresh process and keep the port, and reading
                # the old pid as "the person closed the window" ended the sign-in
                # instantly, with no window ever having been seen.
                if browser_is_up(port):
                    return "", f"could not read the sign-in from the browser ({err})"
                closed = True
                break
            jar = cookies_for(cookies, host)
            if jar:
                best = jar
            if any(n.lower().startswith(SSO_COOKIE_PREFIX) for n in jar):
                say("signed in — closing the window.")
                return cookie_line(jar), ""

            # Walk the two pages a program is able to walk. Each is done once
            # per landing, so a page that ignores it is not hammered.
            where = page_url(ws)
            if where and where != told:
                # Every OTHER page names itself too. Those two are the only ones
                # this can walk past; on anything else the window is simply
                # waiting for the person, and saying nothing is what makes that
                # look like a program that has lost its way.
                told = where
                say(f"the window is on {_site(where)}")
            if LOGIN_PAGE_MARK in where and where != pushed:
                pushed = where
                say("asking OKbase for the company sign-in…")
                navigate(port, sso_start_url(page))
            elif MS_LOGIN_HOST in where and where != picked:
                picked = where
                if account:
                    verdict, err = pick_account(port, account)
                    if verdict == "clicked":
                        say(f"picking your {account} account…")
                    else:
                        bring_to_front(port)
                        say(f"finish the sign-in in the browser window "
                            f"(it is not offering {account}).")
                else:
                    bring_to_front(port)
                    say("pick your work account in the browser window.")
            time.sleep(POLL_S)

        if best:
            # A session but no single sign-on cookie: usable, but only until the
            # portal's own timeout. Say so rather than passing it off as done.
            return cookie_line(best), (
                "signed in, but the company sign-on cookie never appeared — this "
                "will work only until the portal's own timeout")
        if closed:
            return "", ("the browser window was closed before the sign-in "
                        "finished")
        # Name the page it gave up on. "Not completed in time" on its own sends
        # the operator looking for a broken program, when the answer is almost
        # always a prompt that was left sitting in the window.
        stuck = page_url(ws)
        where = f" — the window was still on {stuck[:80]}" if stuck else ""
        return "", f"the sign-in was not completed in time{where}"
    finally:
        if ws is not None:
            ws.call("Browser.close", timeout=3.0)
            ws.close()
        try:
            if proc is not None and proc.poll() is None:
                proc.terminate()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- #

def main(argv: list[str]) -> int:
    account = argv[1] if len(argv) > 1 else ""
    exe, err = find_edge()
    print(f"Edge          : {exe or err}")
    print(f"profile       : {profile_dir()}")
    print(f"account       : {account or '(none given — pick it in the window)'}")
    line, err = renew(progress=lambda t: print(f"  {t}"), account=account)
    if not line:
        print(f"\nFAILED: {err}")
        return 1
    if err:
        print(f"\nWARNING: {err}")
    jar = om.parse_cookies(line)
    print("")
    for row in om.describe_cookies(jar):
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
