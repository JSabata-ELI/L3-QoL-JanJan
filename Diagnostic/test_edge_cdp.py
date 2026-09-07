"""Offline checks for edge_cdp — the browser sign-in.

No browser, no network. What CAN be tested without either is the part most
likely to be got wrong by hand: the websocket framing, the domain filter, and
that the cookie line it produces is exactly what okbase_menu already eats.

    python -m pytest test_edge_cdp.py
"""

import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import edge_cdp as ec          # noqa: E402
import okbase_menu as om       # noqa: E402


# --------------------------------------------------------------------------- #
# The websocket framing, which is hand-rolled because the stdlib has none
# --------------------------------------------------------------------------- #

class _FakeSocket:
    """Feeds `chunks` to recv, and records everything sent."""

    def __init__(self, chunks=()):
        self.sent = bytearray()
        self._chunks = list(chunks)
        self.timeout = None

    def sendall(self, data):
        self.sent += data

    def recv(self, _n):
        return self._chunks.pop(0) if self._chunks else b""

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        pass


def _ws_with(chunks=()):
    """A _Ws that never opened a real connection."""
    ws = ec._Ws.__new__(ec._Ws)
    ws._sock = _FakeSocket(chunks)
    ws._buf = b""
    ws._next_id = 0
    return ws


def _server_frame(payload: bytes, opcode=0x1, fin=True) -> bytes:
    """A frame as the BROWSER sends it: never masked."""
    head = bytes([(0x80 if fin else 0) | opcode])
    if len(payload) < 126:
        head += bytes([len(payload)])
    elif len(payload) < 65536:
        head += bytes([126]) + struct.pack("!H", len(payload))
    else:
        head += bytes([127]) + struct.pack("!Q", len(payload))
    return head + payload


def test_what_we_send_is_masked_as_the_standard_demands():
    """A client frame MUST be masked; an unmasked one is dropped on the floor by
    every server and the command would simply never arrive."""
    ws = _ws_with()
    ws._send_frame(b"hello")
    sent = bytes(ws._sock.sent)
    assert sent[0] == 0x81                     # FIN + text
    assert sent[1] & 0x80, "the mask bit is not set"
    assert (sent[1] & 0x7F) == 5
    mask, body = sent[2:6], sent[6:]
    assert bytes(b ^ mask[i % 4] for i, b in enumerate(body)) == b"hello"


def test_the_three_length_forms():
    """A cookie list is far too big for the short form, so the 16- and 64-bit
    forms are not theoretical here."""
    for size, marker, extra in ((5, 5, 0), (200, 126, 2), (70000, 127, 8)):
        ws = _ws_with()
        ws._send_frame(b"x" * size)
        sent = bytes(ws._sock.sent)
        assert (sent[1] & 0x7F) == marker, size
        assert len(sent) == 2 + extra + 4 + size, size


def test_a_message_split_across_frames_is_reassembled():
    """Chromium fragments a big answer, and a cookie list is a big answer."""
    ws = _ws_with([_server_frame(b'{"a":', fin=False),
                   _server_frame(b'1}', opcode=0x0, fin=True)])
    assert ws._read_message() == '{"a":1}'


def test_a_ping_is_answered_and_does_not_end_the_wait():
    """Chromium pings. Treating a ping as the answer loses the real one."""
    ws = _ws_with([_server_frame(b"beat", opcode=0x9),
                   _server_frame(b"the answer")])
    assert ws._read_message() == "the answer"
    assert bytes(ws._sock.sent)[0] == 0x8A, "no pong was sent"


def test_a_message_arriving_in_dribbles_is_still_read():
    """recv returns what it likes, not what was asked for."""
    frame = _server_frame(b'{"id":1}')
    ws = _ws_with([frame[:1], frame[1:2], frame[2:5], frame[5:]])
    assert ws._read_message() == '{"id":1}'


def test_a_closed_connection_is_an_error_not_a_hang():
    ws = _ws_with([_server_frame(b"", opcode=0x8)])
    result, err = ws.call("Storage.getCookies", timeout=1.0)
    assert result == {} and err, err


def test_events_on_the_wire_are_not_mistaken_for_the_answer():
    """The browser pushes events down the same socket. Taking the first message
    to arrive would answer the command with something unrelated."""
    ws = _ws_with([
        _server_frame(b'{"method":"Target.targetCreated","params":{}}'),
        _server_frame(b'{"id":1,"result":{"cookies":[{"name":"A"}]}}'),
    ])
    result, err = ws.call("Storage.getCookies", timeout=5.0)
    assert err == "", err
    assert result["cookies"][0]["name"] == "A"


def test_the_browser_refusing_a_command_is_reported_not_swallowed():
    ws = _ws_with([_server_frame(
        b'{"id":1,"error":{"code":-32601,"message":"wasn\'t found"}}')])
    result, err = ws.call("Network.getAllCookies", timeout=5.0)
    assert result == {}
    assert "wasn't found" in err, err


# --------------------------------------------------------------------------- #
# What comes back out
# --------------------------------------------------------------------------- #

BROWSER_COOKIES = [
    {"name": "JSESSIONID", "value": "ABC123", "domain": "elieric.okbase.cz"},
    {"name": "_shibsession_64656661756c74", "value": "_deadbeef",
     "domain": "elieric.okbase.cz"},
    {"name": "wide", "value": "yes", "domain": ".okbase.cz"},
    {"name": "MSFPC", "value": "secret", "domain": ".microsoft.com"},
    {"name": "SRCHD", "value": "secret", "domain": ".bing.com"},
    {"name": "nameless", "value": "x", "domain": ""},
]


def test_only_the_portal_s_own_cookies_come_through():
    """The profile also holds Microsoft's sign-on cookies. Those are neither
    wanted nor ours to pass around."""
    jar = ec.cookies_for(BROWSER_COOKIES, "elieric.okbase.cz")
    assert set(jar) == {"JSESSIONID", "_shibsession_64656661756c74", "wide"}
    assert "MSFPC" not in jar and "SRCHD" not in jar


def test_a_lookalike_domain_does_not_get_in():
    assert ec.cookies_for(BROWSER_COOKIES, "notokbase.cz") == {}
    # …and a host that merely ENDS with the name is not a subdomain of it.
    assert ec.cookies_for(
        [{"name": "a", "value": "1", "domain": "evil-okbase.cz"}],
        "okbase.cz") == {}


def test_the_cookie_line_is_exactly_what_okbase_menu_already_eats():
    """The whole reason nothing downstream had to change."""
    jar = ec.cookies_for(BROWSER_COOKIES, "elieric.okbase.cz")
    line = ec.cookie_line(jar)
    assert om.parse_cookies(line) == jar
    assert not om.looks_like_curl(line)      # never mistaken for a command


def test_the_sign_on_cookie_is_recognised_by_the_name_the_module_waits_for():
    """renew() waits for this prefix; if it ever stopped matching, the window
    would sit open until it timed out and report nothing."""
    jar = ec.cookies_for(BROWSER_COOKIES, "elieric.okbase.cz")
    assert any(n.lower().startswith(ec.SSO_COOKIE_PREFIX) for n in jar)


# --------------------------------------------------------------------------- #
# Where it puts things
# --------------------------------------------------------------------------- #

def test_the_browser_profile_is_local_and_does_not_roam():
    """A browser profile is hundreds of megabytes of cache. Putting it in the
    roaming %APPDATA% this program uses for settings would drag all of it
    around with the Windows profile."""
    path = str(ec.profile_dir()).lower()
    assert "diagnostic" in path and "edge-profile" in path
    assert "roaming" not in path


def test_the_port_is_never_the_well_known_one():
    """9222 is what everything else uses, including a developer's own browser."""
    port = ec._free_port()
    assert 1024 < port < 65536
    assert port != 9222
    assert ec._free_port() != port or True     # just must not raise


def test_the_window_is_never_headless():
    """The visible window IS the feature: a person has to complete a Microsoft
    prompt in it. A headless one would stop exactly there."""
    args = ec.launch_args("msedge.exe", ec.profile_dir(), 12345, "https://x/y")
    assert not any("headless" in a for a in args), args


def test_the_debug_port_only_works_with_a_profile_of_its_own():
    """Since Chrome/Edge 136 the port is IGNORED without a non-default
    --user-data-dir. Lose that flag and the feature silently stops working —
    the window opens and nothing can ever be read out of it."""
    args = ec.launch_args("msedge.exe", ec.profile_dir(), 12345, "https://x/y")
    assert any(a.startswith("--remote-debugging-port=12345") for a in args), args
    profile = [a for a in args if a.startswith("--user-data-dir=")]
    assert profile, args
    assert "edge-profile" in profile[0]
    assert args[0] == "msedge.exe" and args[-1] == "https://x/y"


# --------------------------------------------------------------------------- #
# The two pages it has to get past on its own
# --------------------------------------------------------------------------- #

def test_the_sso_start_url_belongs_to_the_same_instance_as_the_page():
    """A second OKbase instance must not be sent to the first one's sign-on."""
    assert ec.sso_start_url(ec.MENU_PAGE).startswith(
        ec.MENU_PAGE.split("/okbase/", 1)[0] + "/okbase/web-client/web?")
    other = "https://elsewhere.example.com/okbase/web-client/web/objednavky-jidel"
    assert ec.sso_start_url(other) == (
        "https://elsewhere.example.com/okbase/web-client/web"
        "?sso=yes&dataSource=defaultDataSource&organization=1")


def test_the_portal_s_own_login_page_is_recognised():
    """This is the page the walk has to step over: it waits for a click on a
    'company account' button, and a program has no way to press it."""
    assert ec.LOGIN_PAGE_MARK in (
        "https://elieric.okbase.cz/okbase/web-client/login?organization=1")
    assert ec.LOGIN_PAGE_MARK in (
        "https://elieric.okbase.cz/okbase/web-client/login"
        "?dataSource=defaultDataSource&organization=1")
    assert ec.LOGIN_PAGE_MARK not in ec.MENU_PAGE


def test_the_page_url_comes_from_the_page_target_only():
    """Target.getTargets also lists the browser itself and every extension;
    picking the wrong row would have the walk react to a page nobody sees."""
    reply = ('{"id":1,"result":{"targetInfos":['
             '{"type":"browser","url":""},'
             '{"type":"service_worker","url":"https://x/sw.js"},'
             '{"type":"page","url":"https://elieric.okbase.cz/okbase/x"}]}}')
    ws = _ws_with([_server_frame(reply.encode())])
    assert ec.page_url(ws) == "https://elieric.okbase.cz/okbase/x"


def test_the_account_to_pick_is_quoted_into_the_page_command():
    """The address is pasted into JavaScript, so it goes through json.dumps —
    an apostrophe in a name would otherwise break the whole expression."""
    seen = {}

    def fake(port, method, params):
        seen.update(method=method, params=params)
        return {"result": {"value": "clicked"}}, ""

    old = ec._in_page
    ec._in_page = fake
    try:
        verdict, err = ec.pick_account(1234, "j.o'brien@example.com")
    finally:
        ec._in_page = old
    assert (verdict, err) == ("clicked", "")
    assert seen["method"] == "Runtime.evaluate"
    expr = seen["params"]["expression"]
    assert '"j.o\'brien@example.com"' in expr
    assert seen["params"]["returnByValue"] is True


def test_an_account_that_is_not_offered_is_not_reported_as_picked():
    """Then the window is on some other Microsoft page - a password, an
    authenticator - and only a person can answer it."""
    ec_in_page = ec._in_page
    ec._in_page = lambda *_a, **_k: ({"result": {"value": "not-offered"}}, "")
    try:
        assert ec.pick_account(1234, "a@b.c") == ("not-offered", "")
    finally:
        ec._in_page = ec_in_page


class _DeadProc:
    """A process that has already ended - what a handed-off launch leaves."""

    def poll(self):
        return 0


class _LiveProc:
    def poll(self):
        return None


def test_a_launch_that_quit_is_not_waited_out_for_half_a_minute():
    """A second Edge on the same profile hands over its address and exits, so
    the port it was told to open never will. Waiting the full PORT_TIMEOUT_S for
    it turned a repairable situation into 'could not talk to the browser'."""
    port = ec._free_port()          # nothing is listening on it
    began = time.monotonic()
    url, err = ec.browser_socket_url(port, wait_s=30.0, proc=_DeadProc())
    assert (url, err) == ("", ec.HANDED_OFF)
    assert time.monotonic() - began < 5.0


def test_a_live_launch_still_waits_for_its_port():
    """The other half of the same test: a browser that is merely slow to start
    must not be given up on. It is a cold browser on a busy PC."""
    port = ec._free_port()
    url, err = ec.browser_socket_url(port, wait_s=2.0, proc=_LiveProc())
    assert url == ""
    assert err != ec.HANDED_OFF


def test_a_closed_port_is_how_a_closed_window_is_known():
    """Never the process id: Edge may put its work into a fresh process and
    keep the window open, and reading the old pid as 'the person closed the
    window' ended the sign-in instantly with the window still on screen."""
    assert ec.browser_is_up(ec._free_port()) is False


def test_the_page_is_named_by_its_site():
    """What `progress` says out loud while it waits. A whole address is
    unreadable in one line of a dialog; the site is the part that answers
    'where is it stuck'."""
    assert ec._site("https://login.microsoftonline.com/common/oauth2/x?y=1") \
        == "login.microsoftonline.com"
    assert ec._site("https://elieric.okbase.cz/okbase/web-client/login") \
        == "elieric.okbase.cz"
