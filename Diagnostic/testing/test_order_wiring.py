"""That `/food order` is actually wired up — in the app and in the listener.

The ordering logic itself is tested in test_okbase_orders.py. What this covers
is the plumbing around it, which is where a feature ends up working perfectly in
a function nobody calls: that the command reaches the handler, that the sender's
address gets there with it, and that every refusal happens BEFORE the portal is
touched.

No portal, no Qt window on screen, no network: `change_order` is replaced, and
the test fails if it is reached when it should not have been.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                   # noqa: BLE001
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}\n       got  {got!r}\n       want {want!r}")


import okbase_menu as om                            # noqa: E402

# Ordering spends a one-time code, and spending WRITES the Windows account's
# real okbase.json. A test must not touch the operator's own settings, so the
# two functions that reach that file are replaced for the whole run, before
# anything is exercised. (Learned the hard way: without this, the first run of
# this file put a list of test codes into the real file.)
_FAKE_DISK: dict = {}
om.load_user_settings = lambda: dict(_FAKE_DISK)
om.save_user_settings = lambda values: (_FAKE_DISK.update(values), "")[1]

OWNER = "me@eli-beams.eu"
# Fixed words rather than generated ones, so `PIN` can be a constant the whole
# file uses, and a fresh salt each time a list is handed out — every check that
# orders something starts from five unspent codes. Few rounds: what is under
# test is the wiring, not how long a fingerprint takes to compute.
WORDS = ["bakoli", "severu", "tumida", "nakepy", "vozile"]
SECRET = WORDS[0]
PIN = f"pin:{SECRET}"
REACHED: list[tuple] = []


def fresh_codes() -> dict:
    import base64
    import secrets
    salt = secrets.token_bytes(16)
    return {"salt": base64.b64encode(salt).decode("ascii"), "rounds": 1000,
            "hashes": [om._code_digest(w, salt, 1000) for w in WORDS]}


# "the command asks for exactly what the day already holds" — the one case the
# real change_order still turns away, and it turns it away before the code is
# spent. Anything else on a booked day is re-ordered.
SAME_AS_BOOKED: list[bool] = [False]


def fake_change_order(settings, day, picks=(), clear=False, session_out=None,
                      authorise=None):
    meal_was = om.Meal(name="Hrachová", kind="soup", item_id=572633,
                       code="POLEVKA")
    if SAME_AS_BOOKED[0] and not clear:
        # The real one stops here, before the code is spent.
        return om.OrderOutcome(day=day.isoformat(), already=True,
                               was=(meal_was,), ordered=(meal_was,))
    # The real one calls this at the last moment before saving; a stand-in that
    # skipped it would let a spent-code bug through unnoticed.
    if authorise is not None:
        ok, why = authorise()
        if not ok:
            return om.OrderOutcome(day=day.isoformat(), error=why)
    REACHED.append((day, picks, clear))
    if session_out is not None:
        session_out["cookies"] = "JSESSIONID=fake"
    meal = om.Meal(name="Sekaná / Meat loaf", price="147 Kč",
                   kind="main course", item_id=572642, code="HLAVNI_JIDLO")
    if clear:
        return om.OrderOutcome(day=day.isoformat(), was=(meal,))
    return om.OrderOutcome(day=day.isoformat(), ordered=(meal,))


# --------------------------------------------------------------------------- #
print("the app (monitor_tab)")

from PySide6.QtCore import QObject                  # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402
import monitor_tab as mt                            # noqa: E402

app = QApplication.instance() or QApplication([])


class FakeTab(QObject):
    """Just enough of MonitorWidget to exercise _cmd_food / _cmd_food_order.

    A QObject because the handler parents its signals object to `self` — the
    thing that stops every job leaking one for the life of the tab.
    """

    ORDER_JOB_MAX_S = mt.MonitorWidget.ORDER_JOB_MAX_S
    _cmd_food = mt.MonitorWidget._cmd_food
    _cmd_food_order = mt.MonitorWidget._cmd_food_order
    _freshen_then_reply = mt.MonitorWidget._freshen_then_reply
    _on_order_done = mt.MonitorWidget._on_order_done
    _remember_session = mt.MonitorWidget._remember_session

    def __init__(self, **settings):
        super().__init__()
        # A fresh list per tab, and the same one on the fake disk: a code spent
        # by one check must not make the next one fail for the wrong reason.
        codes = fresh_codes()
        _FAKE_DISK["okbase_order_codes"] = codes
        self.settings = {"okbase_enabled": True,
                         "okbase_order_emails": [OWNER],
                         "okbase_order_codes": codes}
        self.settings.update(settings)
        self._menu_cache = {"days": {"2026-09-11": [{"name": "Sekaná",
                                                     "kind": "main course"}]}}
        self._order_job_ns = 0
        self._menu_reply_pending = False
        self._menu_reply_args = ""
        self._menu_reply_suffix = ""
        self.replies: list[str] = []
        self.reloaded = 0
        self.threads: list[tuple] = []
        # False makes _freshen_then_reply give up and answer from the saved
        # copy — the "OKbase did not answer" road, tested on its own below.
        self.menu_job_starts = True

    def _reply(self, text):
        self.replies.append(text)

    def _log(self, text):
        pass

    def _food_reply(self, args):
        return f"[menu for {args}]"

    def _food_status(self):
        return "[status]"

    def _reload_okbase_sign_in(self):
        self.reloaded += 1

    def _start_menu_job(self, mode):
        self.threads.append(("menu", mode))
        return self.menu_job_starts

    def deliver_menu(self):
        """What _on_menu_done does once the portal has answered."""
        if not self._menu_reply_pending:
            return
        self._menu_reply_pending = False
        suffix, self._menu_reply_suffix = self._menu_reply_suffix, ""
        self._reply(self._food_reply(self._menu_reply_args) + suffix)


def run_cmd(tab, line: str, email: str = OWNER):
    import bot_commands
    pc = bot_commands.parse_command(line)
    # The real dispatch calls _cmd_food(pc, email) — same call, same order.
    tab._cmd_food(pc, email)
    # Reading commands now ask OKbase first and answer when it lands; the read
    # itself is a thread, so the test plays the landing.
    tab.deliver_menu()
    return tab.replies[-1] if tab.replies else ""


# The thread must not really start: replace it, and record instead.
_real_thread = mt.threading.Thread
started: list[dict] = []


class FakeThread:
    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target, self.args, self.name = target, args, name

    def start(self):
        started.append({"name": self.name, "args": self.args})


mt.threading.Thread = FakeThread

tab = FakeTab()
reply = run_cmd(tab, "/food order friday 1 " + PIN)
check("the order reaches the worker", len(started), 1)
check("on its own thread", started[0]["name"], "okbase-order")
check("the sign-in is re-read first", tab.reloaded, 1)
check("the person is told it is working", "Ordering lunch" in reply, True)
sig, settings, day, picks, clear, lang, code = started[0]["args"]
check("the day got through", day, date(2026, 9, 11))
check("the meal got through", picks, (("HLAVNI_JIDLO", 1),))
check("and it is not a cancel", clear, False)
check("the one-time code got through", code, SECRET)
check("and nothing has been struck off yet — that waits for the save",
      om.codes_left(tab.settings), len(WORDS))

started.clear()
tab = FakeTab()
reply = run_cmd(tab, "/food cancel friday " + PIN)
check("cancel reaches the worker as a cancel", started[0]["args"][4], True)

started.clear()
tab = FakeTab()
reply = run_cmd(tab, "/food order friday 1 " + PIN,
                email="someone@eli-beams.eu")
check("a sender outside a set list is refused", reply.startswith("⛔"), True)
check("and the portal is never touched", started, [])

started.clear()
tab = FakeTab()
reply = run_cmd(tab, "/food order friday 1")
check("no password, no order", started, [])
check("and it says how to give one", "pin:" in reply, True)

started.clear()
tab = FakeTab()
reply = run_cmd(tab, "/food order friday 1 pin:wrongword")
check("a wrong password, no order", started, [])
check("and the reply never repeats it", "wrongword" in reply, False)

started.clear()
tab = FakeTab()
reply = run_cmd(tab, "/food cancel friday")
check("cancelling needs the password too", started, [])

# No list of senders is the default: the one-time code is the protection, so
# any sender who writes a valid one may order.
started.clear()
tab = FakeTab(okbase_order_emails=[])
reply = run_cmd(tab, "/food order friday 1 " + PIN, email="shared@eli-beams.eu")
check("with no list, any sender with a code may order", len(started), 1)
started.clear()
tab = FakeTab(okbase_order_emails=[])
reply = run_cmd(tab, "/food order friday 1 pin:wrongword",
                email="shared@eli-beams.eu")
check("but still not without a valid one", started, [])

started.clear()
tab = FakeTab(okbase_enabled=False)
reply = run_cmd(tab, "/food order friday 1 " + PIN)
check("switched off means no order", started, [])
check("and says so", "switched off" in reply, True)

started.clear()
tab = FakeTab()
reply = run_cmd(tab, "/food order friday")
check("a day with no meal shows the menu", "[menu for 2026-09-11]" in reply,
      True)
check("and orders nothing", started, [])
check("but says how to pick", "/food order 11.09. 1 pin:" in reply, True)

# Looking at a menu changes nothing, so it must not be behind the password —
# `/food friday` already shows it to anybody.
started.clear()
tab = FakeTab(okbase_order_emails=[])
reply = run_cmd(tab, "/food order friday", email="anyone@x.y")
check("showing a day needs no password at all",
      "[menu for 2026-09-11]" in reply, True)

started.clear()
tab = FakeTab()
run_cmd(tab, "/food order friday 1 " + PIN)
reply = run_cmd(tab, "/food order friday 2 " + PIN)
check("a second order while one runs is held off", len(started), 1)
check("and says why", "one moment" in reply, True)

# The guard is a stamp, not a flag: a job that dies without answering must not
# refuse every later order for the life of the app.
tab._order_job_ns -= (tab.ORDER_JOB_MAX_S + 1) * 1_000_000_000
started.clear()
run_cmd(tab, "/food order friday 2 " + PIN)
check("a dead job does not wedge the command for ever", len(started), 1)

# Reading commands must be untouched by any of this.
started.clear()
tab = FakeTab(okbase_order_emails=[])
check("/food still reads for everyone",
      run_cmd(tab, "/food friday", email="anyone@x.y"), "[menu for friday]")
check("/food status too", run_cmd(tab, "/food status", email="anyone@x.y"),
      "[status]")

# …but they now ask OKbase first, so nobody reads a menu from hours ago
# without knowing it.
tab = FakeTab()
run_cmd(tab, "/food friday")
check("a reading command reads OKbase first", tab.threads, [("menu", "fetch")])
check("and the sign-in is re-read before it", tab.reloaded, 1)
check("and the day asked for is the day answered", tab.replies[-1],
      "[menu for friday]")

tab = FakeTab()
run_cmd(tab, "/food order friday")     # a day with no meal named
check("so does the list you pick a number from", tab.threads,
      [("menu", "fetch")])
check("and the pick line still comes with it",
      "/food order 11.09. 1 pin:" in tab.replies[-1], True)

# The fallback: no read possible, so the saved copy answers on the spot.
tab = FakeTab()
tab.menu_job_starts = False
check("a read that cannot start falls back to the saved copy",
      run_cmd(tab, "/food friday"), "[menu for friday]")

tab = FakeTab(okbase_enabled=False)
check("switched off reads the saved copy and never the portal",
      run_cmd(tab, "/food friday"), "[menu for friday]")
check("and does not even try", tab.threads, [])
check("/food status never reads the portal",
      run_cmd(FakeTab(), "/food status"), "[status]")

# What the worker does when it lands.
started.clear()
tab = FakeTab()
run_cmd(tab, "/food order friday 1 " + PIN)
sig, settings, day, picks, clear, lang, code = started[0]["args"]
_real_change, om.change_order = om.change_order, fake_change_order
try:
    tab.replies.clear()
    outcome = om.change_order(settings, day, picks, clear)
    mt.MonitorWidget._on_order_done(tab, (outcome, clear, lang, ""))
finally:
    om.change_order = _real_change
check("the reply names the meal", "Sekaná" in tab.replies[-1], True)
check("and the saved copy is read again afterwards", tab.threads,
      [("menu", "fetch")])
check("and the guard is released", tab._order_job_ns, 0)

mt.threading.Thread = _real_thread


# --------------------------------------------------------------------------- #
print("")
print("the always-on listener (remote_launcher)")

import remote_launcher as rl                        # noqa: E402

def lset(**extra) -> dict:
    """The listener's settings, with five unspent codes on the fake disk."""
    codes = fresh_codes()
    _FAKE_DISK["okbase_order_codes"] = codes
    out = {"okbase_enabled": True, "okbase_order_emails": [OWNER],
           "okbase_order_codes": codes}
    out.update(extra)
    return out

_real_change, om.change_order = om.change_order, fake_change_order
_real_refresh, om.refresh = om.refresh, lambda s, **kw: ({}, "")
_real_load, om.load_cache = om.load_cache, lambda s, **kw: {
    "fetched": "2026-09-09T08:00:00",
    "days": {"2026-09-11": [{"name": "Sekaná", "kind": "main course"}]}}
_real_save, rl._save_okbase_cookies = rl._save_okbase_cookies, \
    lambda c, s=None: None
try:
    REACHED.clear()
    reply = rl._food_answer("order friday 1 " + PIN, lset(), OWNER)
    check("the listener orders too", len(REACHED), 1)
    check("with the right day and meal", REACHED[0][:2],
          (date(2026, 9, 11), (("HLAVNI_JIDLO", 1),)))
    check("and names the meal back", "Sekaná" in reply, True)

    REACHED.clear()
    reply = rl._food_answer("order friday 1 " + PIN, lset(), "nobody@x.y")
    check("a sender outside a set list is refused there as well",
          reply.startswith("⛔"), True)
    check("and the portal is never touched", REACHED, [])

    REACHED.clear()
    reply = rl._food_answer("order friday 1", lset(), OWNER)
    check("no password, no order from the listener either", REACHED, [])
    REACHED.clear()
    reply = rl._food_answer("order friday 1 pin:wrongword", lset(), OWNER)
    check("a wrong password is refused there too", REACHED, [])
    check("and it is never echoed", "wrongword" in reply, False)

    REACHED.clear()
    reply = rl._food_answer("cancel friday " + PIN, lset(), OWNER)
    check("cancel works from the listener", REACHED[0][2], True)
    check("and says it cancelled", "cancelled" in reply, True)

    # Ordering exactly what the day already holds: the listener must answer
    # with what is on it AND the menu, and must not have spent a code.
    SAME_AS_BOOKED[0] = True
    try:
        REACHED.clear()
        reply = rl._food_answer("order friday 1 " + PIN, lset(), OWNER)
        check("asking for what is already there sends nothing", REACHED, [])
        check("it says so", "already what you have ordered" in reply, True)
        check("and the menu comes with it", "Sekaná" in reply, True)
        check("and it says no code was used", "no code was used" in reply, True)
    finally:
        SAME_AS_BOOKED[0] = False

    # A booked day with a DIFFERENT meal: plain `order` re-orders it, no
    # `change` word needed, and `change` still means the same thing.
    REACHED.clear()
    reply = rl._food_answer("order friday 1 " + PIN, lset(), OWNER)
    check("a booked day is re-ordered by a plain `order`", len(REACHED), 1)
    REACHED.clear()
    reply = rl._food_answer("change friday 1 " + PIN, lset(), OWNER)
    check("`change` is a synonym", len(REACHED), 1)

    REACHED.clear()
    reply = rl._food_answer("order friday 1 " + PIN,
                            lset(okbase_enabled=False), OWNER)
    check("switched off means no order there either", REACHED, [])

    reply = rl._food_answer("friday", lset(), "anyone@x.y")
    check("and reading still answers anybody", "Sekaná" in reply, True)

    # Reading asks OKbase first here too. `om.refresh` above answers with
    # nothing, so this reply IS the fallback — the saved copy, plus the reason
    # it could not get newer.
    om.refresh = lambda s, **kw: ({}, "the portal did not answer")
    reply = rl._food_answer("friday", lset(), OWNER)
    check("a failed read falls back to the saved copy", "Sekaná" in reply,
          True)
    check("and says why it is the saved one",
          "Could not read OKbase again" in reply, True)

    om.refresh = lambda s, **kw: ({
        "fetched": "2026-09-10T13:43:00",
        "days": {"2026-09-11": [{"name": "Kuřecí nudličky",
                                 "kind": "main course"}]}}, "")
    reply = rl._food_answer("friday", lset(), OWNER)
    check("a read that works is what gets answered",
          "Kuřecí nudličky" in reply, True)
    check("and the saved copy is not shown instead", "Sekaná" in reply, False)
    check("and nothing is said about a failure",
          "Could not read OKbase" in reply, False)
finally:
    om.change_order, om.refresh, om.load_cache = (
        _real_change, _real_refresh, _real_load)
    rl._save_okbase_cookies = _real_save


print("")
if FAILED:
    print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
    raise SystemExit(1)
print("all ok")
