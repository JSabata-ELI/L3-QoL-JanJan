"""The sign-in window must not hand back yesterday's sign-on cookie.

WHY
    2026-09-11, after an overnight restart: "Sign in with Edge" came back in a
    few seconds saying it had signed in, and the check that runs straight after
    it said "the saved OKbase session has expired - sign in again". Every retry
    did the same, so the button could not repair anything.

    The cause was that the walk accepted the sign-on cookie because of its NAME.
    The window keeps a browser profile of its own, so the cookie from the day
    before is still sitting in it every morning; the portal, meanwhile, had
    forgotten the session behind it. Presence is not liveness - these tests hold
    the walk to asking the portal.

    Nothing here opens a browser: every door out of the module is stubbed.
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import edge_cdp  # noqa: E402

HOST = "elieric.okbase.cz"
STALE = "_shibsession_64656661=yesterday"
FRESH = "_shibsession_64656661=today"


class _Proc:
    def poll(self):
        return None

    def terminate(self):
        pass


class _Ws:
    """Enough of the browser socket for the walk. Records what it was asked."""

    def __init__(self, url="ws://test", timeout=20.0):
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, params=None, timeout=20.0):
        self.calls.append((method, dict(params or {})))
        return {}, ""

    def close(self):
        pass


def _cookie(name: str, value: str, domain: str = HOST) -> dict:
    return {"name": name, "value": value, "domain": domain, "path": "/"}


class _Portal:
    """The browser profile and the portal, as the walk sees them."""

    def __init__(self, jar: list[dict], alive: list[str]):
        self.jar = list(jar)
        self.alive = list(alive)      # the verdict for each check, in order
        self.asked = 0
        self.went: list[str] = []     # where the window was sent

    def cookies(self, ws):
        return list(self.jar), ""

    def verdict(self, port, base=""):
        self.asked += 1
        return self.alive[min(self.asked - 1, len(self.alive) - 1)]

    def navigate(self, port, url):
        self.went.append(url)
        return ""


@pytest.fixture
def walk(monkeypatch):
    """Stub every way out of the module. Returns a function taking a _Portal."""
    monkeypatch.setattr(edge_cdp, "POLL_S", 0.0)
    monkeypatch.setattr(edge_cdp, "launch", lambda url, port: (_Proc(), ""))
    monkeypatch.setattr(edge_cdp, "browser_socket_url",
                        lambda port, wait_s=0.0, proc=None: ("ws://test", ""))
    monkeypatch.setattr(edge_cdp, "_Ws", _Ws)
    monkeypatch.setattr(edge_cdp, "page_url", lambda ws: "")
    monkeypatch.setattr(edge_cdp, "bring_to_front", lambda port: "")
    monkeypatch.setattr(edge_cdp, "browser_is_up", lambda port: True)

    def run(portal: _Portal, timeout_s: float = 2.0):
        monkeypatch.setattr(edge_cdp, "all_cookies", portal.cookies)
        monkeypatch.setattr(edge_cdp, "session_alive", portal.verdict)
        monkeypatch.setattr(edge_cdp, "navigate", portal.navigate)
        return edge_cdp.renew(timeout_s=timeout_s)

    return run


def test_a_sign_on_cookie_the_portal_has_forgotten_is_not_a_sign_in(walk):
    portal = _Portal([_cookie("_shibsession_64656661", "yesterday")], ["dead"])
    line, err = walk(portal, timeout_s=0.3)
    assert STALE not in line
    assert err  # it ran out of time waiting for a real one, and says so


def test_the_window_is_sent_back_to_the_company_sign_on(walk):
    portal = _Portal([_cookie("_shibsession_64656661", "yesterday")], ["dead"])
    walk(portal, timeout_s=0.3)
    assert portal.went, "the walk gave up instead of starting a new sign-in"
    assert edge_cdp.SSO_START_QUERY in portal.went[0]


def test_the_sign_in_that_follows_is_the_one_handed_back(walk):
    portal = _Portal([_cookie("_shibsession_64656661", "yesterday")],
                     ["dead", "alive"])

    def cookies(ws):
        # The new sign-in lands as soon as the window has been sent on its way.
        if portal.went:
            return [_cookie("_shibsession_64656661", "today"),
                    _cookie("JSESSIONID", "n2")], ""
        return list(portal.jar), ""

    portal.cookies = cookies
    line, err = walk(portal, timeout_s=2.0)
    assert err == ""
    assert FRESH in line
    assert "yesterday" not in line


def test_a_live_sign_in_is_handed_back_at_once(walk):
    portal = _Portal([_cookie("_shibsession_64656661", "today"),
                      _cookie("JSESSIONID", "n1")], ["alive"])
    line, err = walk(portal)
    assert err == ""
    assert FRESH in line
    assert portal.went == [], "a working sign-in must not be thrown away"


def test_a_portal_that_cannot_answer_does_not_cost_a_good_sign_in(walk):
    # UNKNOWN is not DEAD. A bad minute on the portal, or a page the request
    # cannot be made from, must never wipe the profile and ask for a new prompt.
    portal = _Portal([_cookie("_shibsession_64656661", "today")], ["unknown"])
    line, err = walk(portal)
    assert err == ""
    assert FRESH in line
    assert portal.went == []


def test_a_sign_in_made_in_this_window_is_not_second_guessed(walk):
    # The person finished the prompt while the walk was watching. Asking the
    # portal about that cookie races its own redirect, and a lost race would
    # throw the sign-in away - so what appears during the walk is not asked
    # about at all. Only what was already in the profile is.
    portal = _Portal([], ["dead"])
    seen = {"polls": 0}

    def cookies(ws):
        seen["polls"] += 1
        if seen["polls"] == 1:
            return [], ""
        return [_cookie("_shibsession_64656661", "today")], ""

    portal.cookies = cookies
    line, err = walk(portal, timeout_s=2.0)
    assert err == ""
    assert FRESH in line
    assert portal.asked == 0, "a fresh sign-in must not be put to the portal"


def test_only_the_portal_s_cookies_are_thrown_out(monkeypatch):
    ws = _Ws()
    jar = [_cookie("_shibsession_64656661", "yesterday"),
           _cookie("JSESSIONID", "n1"),
           _cookie("ESTSAUTHPERSISTENT", "keep", ".login.microsoftonline.com")]
    monkeypatch.setattr(edge_cdp, "all_cookies", lambda _ws: (jar, ""))
    gone, err = edge_cdp.forget_site(ws, HOST)
    assert err == ""
    assert gone == 2
    deleted = [p.get("name") for m, p in ws.calls if m == "Network.deleteCookies"]
    assert sorted(deleted) == ["JSESSIONID", "_shibsession_64656661"]
    assert "ESTSAUTHPERSISTENT" not in deleted, \
        "clearing Microsoft's sign-in turns a silent renewal into a phone prompt"


def _answer(monkeypatch, value: str) -> dict:
    """Make the page answer `value` to the liveness question. Returns the box
    the request lands in, so the test can read what was asked."""
    seen: dict = {}

    def in_page(port, method, params):
        seen["method"] = method
        seen["params"] = params
        return {"result": {"value": value}}, ""

    monkeypatch.setattr(edge_cdp, "_in_page", in_page)
    return seen


def test_the_question_asked_is_the_one_okbase_menu_asks(monkeypatch):
    # Two places ask "is this sign-in alive" - this module from inside the
    # browser, okbase_menu over its own session. One address, or they drift.
    import okbase_menu as om

    seen = _answer(monkeypatch, "200")
    assert edge_cdp.session_alive(1234) == edge_cdp.ALIVE
    assert om.BASE_DEFAULT + om.ALIVE_PATH in seen["params"]["expression"]
    assert seen["params"]["awaitPromise"] is True
    assert edge_cdp.rest_base(edge_cdp.MENU_PAGE) == om.BASE_DEFAULT


@pytest.mark.parametrize("answer,verdict", [
    ("200", edge_cdp.ALIVE),
    ("401", edge_cdp.DEAD),
    ("403", edge_cdp.DEAD),
    ("redirect", edge_cdp.DEAD),       # bounced to the sign-on service
    ("502", edge_cdp.UNKNOWN),         # the proxy having a bad minute
    ("error:TypeError", edge_cdp.UNKNOWN),   # asked from a page that may not
])
def test_what_each_answer_means(monkeypatch, answer, verdict):
    _answer(monkeypatch, answer)
    assert edge_cdp.session_alive(1234) == verdict
