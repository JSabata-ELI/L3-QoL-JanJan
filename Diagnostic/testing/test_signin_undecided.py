"""A sign-in window that has not answered yet must not be treated as a yes.

WHY
    2026-09-16, reported from the office PC: "Sign in with Edge" put up a window
    with nothing in it, the window closed again in a blink, and behind it came
    "Could not read the menu: the saved OKbase session has expired - sign in
    again". Every press did the same, with Edge open and with Edge closed, so
    the button could repair nothing.

    The chain: the window keeps a browser profile of its own, so an old
    `_shibsession_` cookie is in it from the day before. The walk asked whether
    that cookie still worked - but it asked from INSIDE the window, one second
    after opening it, when the page had not loaded yet. The browser refuses a
    request to the portal from a blank page before it ever leaves the PC, and
    that refusal is indistinguishable from a portal that said nothing. The walk
    read "don't know" as "yes", handed yesterday's cookie back, and closed the
    window - which is why nothing was ever visible in it.

    So: the portal is asked from HERE, with the cookie line, exactly the way the
    canteen menu itself will ask (revive attempt included). While the answer is
    still "don't know", the window stays open. Nothing here opens a browser or
    touches the network.
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import edge_cdp  # noqa: E402
import okbase_menu as om  # noqa: E402

HOST = "elieric.okbase.cz"
STALE = "_shibsession_64656661=yesterday"
BLANK = "about:blank"


class _Proc:
    def poll(self):
        return None

    def terminate(self):
        pass


class _Ws:
    def __init__(self, url="ws://test", timeout=20.0):
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, params=None, timeout=20.0):
        self.calls.append((method, dict(params or {})))
        return {}, ""

    def close(self):
        pass


def _cookie(name: str, value: str, domain: str = HOST) -> dict:
    return {"name": name, "value": value, "domain": domain, "path": "/"}


@pytest.fixture
def window(monkeypatch):
    """The walk with every door out of it stubbed.

    `run` takes the verdicts the portal gives, in order, and where the window's
    page is. It returns (line, err, box) where box records what happened.
    """
    monkeypatch.setattr(edge_cdp, "POLL_S", 0.0)
    monkeypatch.setattr(edge_cdp, "CHECK_EVERY_S", 0.0)
    monkeypatch.setattr(edge_cdp, "launch", lambda url, port: (_Proc(), ""))
    monkeypatch.setattr(edge_cdp, "browser_socket_url",
                        lambda port, wait_s=0.0, proc=None: ("ws://test", ""))
    monkeypatch.setattr(edge_cdp, "_Ws", _Ws)
    monkeypatch.setattr(edge_cdp, "bring_to_front", lambda port: "")
    monkeypatch.setattr(edge_cdp, "browser_is_up", lambda port: True)
    monkeypatch.setattr(edge_cdp, "forget_site", lambda ws, host: (0, ""))

    def run(verdicts, page=BLANK, timeout_s=0.3, grace=5.0, jar=None):
        box = {"asked": 0, "page_asked": 0, "went": []}

        def state(line, base="", timeout=0.0):
            box["asked"] += 1
            return verdicts[min(box["asked"] - 1, len(verdicts) - 1)]

        def from_page(port, base=""):
            box["page_asked"] += 1
            return edge_cdp.UNKNOWN

        monkeypatch.setattr(edge_cdp, "UNDECIDED_GRACE_S", grace)
        monkeypatch.setattr(
            edge_cdp, "all_cookies",
            lambda ws: (list(jar or [_cookie("_shibsession_64656661",
                                             "yesterday")]), ""))
        monkeypatch.setattr(edge_cdp, "cookie_state", state)
        monkeypatch.setattr(edge_cdp, "session_alive", from_page)
        monkeypatch.setattr(edge_cdp, "page_url", lambda ws: page)
        monkeypatch.setattr(edge_cdp, "navigate",
                            lambda port, url: box["went"].append(url) or "")
        line, err = edge_cdp.renew(timeout_s=timeout_s)
        return line, err, box

    return run


def test_an_unanswered_check_does_not_close_the_window(window):
    # The bug, in one line: nothing could answer, so nothing may be handed back.
    line, err, box = window([edge_cdp.UNKNOWN], grace=5.0, timeout_s=0.3)
    assert STALE not in line
    assert err, "an undecided check was passed off as a completed sign-in"


def test_an_answer_that_arrives_late_is_still_used(window):
    # The window is left open, so the check that could not answer at one second
    # old is asked again - and the sign-in that was fine all along is kept.
    line, err, box = window([edge_cdp.UNKNOWN, edge_cdp.UNKNOWN,
                             edge_cdp.ALIVE], grace=5.0, timeout_s=2.0)
    assert err == ""
    assert STALE in line
    assert box["went"] == [], "a working sign-in must not be thrown away"


def test_a_portal_nothing_can_reach_is_not_a_reason_to_sign_in_again(window):
    # Undecided for good: after the wait, the old cookie is handed back rather
    # than the person being sent to a prompt over a portal that is simply down.
    line, err, box = window([edge_cdp.UNKNOWN], grace=0.05, timeout_s=2.0)
    assert err == ""
    assert STALE in line
    assert box["went"] == []


def test_the_page_is_only_asked_while_it_is_on_the_portal(window):
    # A blank page and Microsoft's page cannot make that request at all, and
    # asking them is how "don't know" got mistaken for "signed in".
    window([edge_cdp.UNKNOWN], page=BLANK, grace=0.05, timeout_s=0.5)
    line, err, box = window([edge_cdp.UNKNOWN],
                            page=f"https://{HOST}/okbase/web-client/web",
                            grace=0.05, timeout_s=0.5)
    assert box["page_asked"], \
        "the window's own route is worth trying once it is on the portal"


def test_the_page_is_not_asked_from_a_blank_window(window):
    line, err, box = window([edge_cdp.UNKNOWN], page=BLANK, grace=0.05,
                            timeout_s=0.5)
    assert box["page_asked"] == 0


# --------------------------------------------------------------------------- #
# The verdict itself
# --------------------------------------------------------------------------- #

def test_the_verdict_is_the_one_the_menu_itself_will_reach(monkeypatch):
    """`cookie_state` must go through okbase_menu, not re-ask one address.

    okbase_menu tries the sign-on cookie for a NEW session when the session id
    has timed out (REVIVE_PATHS). A one-address check has no such step, so a
    perfectly good sign-on with a stale session id looks dead to it - and that
    verdict costs a Microsoft prompt for nothing.
    """
    seen = {}

    def session_from_cookie(line, base=om.BASE_DEFAULT, timeout=0.0):
        seen["line"] = line
        seen["base"] = base
        return object(), ""

    monkeypatch.setattr(om, "session_from_cookie", session_from_cookie)
    assert edge_cdp.cookie_state("JSESSIONID=x; _shibsession_1=y") == \
        edge_cdp.ALIVE
    assert "_shibsession_1=y" in seen["line"]
    assert seen["base"] == om.BASE_DEFAULT


@pytest.mark.parametrize("outcome,verdict", [
    (("session", ""), edge_cdp.ALIVE),
    ((None, om.SESSION_EXPIRED), edge_cdp.DEAD),
    ((None, om.PORTAL_UNREACHABLE + " (HTTP 502)"), edge_cdp.UNKNOWN),
    ((None, "the requests library is missing"), edge_cdp.UNKNOWN),
])
def test_what_each_outcome_means(monkeypatch, outcome, verdict):
    monkeypatch.setattr(om, "session_from_cookie",
                        lambda line, base=om.BASE_DEFAULT, timeout=0.0: outcome)
    assert edge_cdp.cookie_state("JSESSIONID=x") == verdict


def test_an_empty_cookie_line_is_no_sign_in(monkeypatch):
    # Guarded here so the portal is never asked a question with no cookie in it.
    monkeypatch.setattr(om, "session_from_cookie",
                        lambda *a, **k: pytest.fail("the portal was asked "
                                                    "with nothing to ask about"))
    assert edge_cdp.cookie_state("   ") == edge_cdp.DEAD
