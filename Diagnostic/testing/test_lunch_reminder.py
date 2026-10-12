"""The morning "no lunch ordered for today" reminder — offline.

Covers the decision (okbase_menu.lunch_verdict), when it is due, the message
text, and the listener path end to end with the portal stubbed out.
Run: python testing/test_lunch_reminder.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import okbase_menu  # noqa: E402

# Never touch the real %APPDATA%\Diagnostic\okbase.json.
_DISK: dict = {}
okbase_menu.load_user_settings = lambda: dict(_DISK)


def _save(values):
    _DISK.update({k: v for k, v in values.items() if k in okbase_menu.OKBASE_KEYS})
    return ""


okbase_menu.save_user_settings = _save

import remote_launcher  # noqa: E402

FAILS = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


MON_0900 = datetime(2026, 10, 5, 9, 0)      # a Monday
TODAY = MON_0900.date().isoformat()
MEAL = [okbase_menu.Meal(name="Soup")] if hasattr(okbase_menu, "Meal") else ["x"]
ORDERED = okbase_menu.Order(day=TODAY, item_ids=(1, 2), none_ordered=False)
NOTHING = okbase_menu.Order(day=TODAY)
OTHER = okbase_menu.Order(day="2026-10-06", item_ids=(3,), none_ordered=False)

# --- the decision -----------------------------------------------------------
V = okbase_menu.lunch_verdict
check("not ordered -> send",
      V(TODAY, {TODAY: MEAL}, {TODAY: "open"}, {TODAY: NOTHING})[0] == "send")
check("day missing from orders -> send",
      V(TODAY, {TODAY: MEAL}, {TODAY: "open"}, {"2026-10-06": OTHER})[0] == "send")
check("ordered -> ordered",
      V(TODAY, {TODAY: MEAL}, {TODAY: "open"}, {TODAY: ORDERED})[0] == "ordered")
check("no menu (holiday) -> no_menu",
      V(TODAY, {}, {}, {})[0] == "no_menu")
check("closed -> closed",
      V(TODAY, {TODAY: MEAL}, {TODAY: "closed"}, {TODAY: NOTHING})[0] == "closed")
verdict, err = V(TODAY, {TODAY: MEAL}, {TODAY: "open"}, {})
check("orders not reported -> undecided", verdict == "" and bool(err))

# --- when it is due ---------------------------------------------------------
ON = {"okbase_enabled": True}
due = okbase_menu.lunch_reminder_due
check("Monday 09:00 due", due(ON, MON_0900))
check("Monday 07:59 not due", not due(ON, datetime(2026, 10, 5, 7, 59)))
check("Saturday not due", not due(ON, datetime(2026, 10, 10, 9, 0)))
check("switched off not due", not due({**ON, "okbase_remind_enabled": False}, MON_0900))
check("canteen off not due", not due({}, MON_0900))
check("custom hour respected", not due({**ON, "okbase_remind_hour": 10}, MON_0900))
okbase_menu.mark_lunch_reminded(MON_0900)
check("already decided today not due", not due(ON, MON_0900))
check("next day due again", due(ON, datetime(2026, 10, 6, 8, 0)))
_DISK.clear()

# --- the message ------------------------------------------------------------
text = okbase_menu.lunch_reminder_text(
    {"okbase_account": "jan.moucka@eli-laser.eu"}, MON_0900)
check("mention falls back to the work account",
      "<@personEmail:jan.moucka@eli-laser.eu|Jan>" in text)
check("day named in English", "Monday 5 Oct" in text)
text = okbase_menu.lunch_reminder_text(
    {"okbase_account": "a@x.eu", "okbase_remind_email": "b.c@y.eu"}, MON_0900)
check("explicit mention address wins", "<@personEmail:b.c@y.eu|B>" in text)
text = okbase_menu.lunch_reminder_text({}, MON_0900)
check("no address -> no mention", "<@" not in text and "no lunch ordered" in text)

# --- the listener, portal stubbed ------------------------------------------


class FakeWebex:
    def __init__(self):
        self.sent = []

    def post_text(self, md):
        self.sent.append(md)


remote_launcher.is_app_running = lambda: False
remote_launcher._reload_okbase_settings = lambda s: False
remote_launcher._save_okbase_cookies = lambda c, s: None
calls = {"check": 0}
answers = []


def fake_check(settings, now=None, session_out=None):
    calls["check"] += 1
    return answers.pop(0)


okbase_menu.check_lunch_today = fake_check
okbase_menu.refresh = lambda *a, **k: ({}, "should not be reached")
okbase_menu.keepalive = lambda *a, **k: ("", "")


class FixedNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return MON_0900


remote_launcher.datetime = FixedNow
settings = {"okbase_enabled": True, "okbase_account": "jan.moucka@eli-laser.eu"}

# A failed read: nothing sent, day not marked, tried again next tick.
wx = FakeWebex()
state = {"day": "", "next_try": 0.0, "ok": None}
answers[:] = [("", "portal down"), ("send", "")]
remote_launcher._menu_upkeep(settings, state, wx)
check("failed read sends nothing", wx.sent == [])
check("failed read does not mark the day", "okbase_remind_sent_day" not in _DISK)
state["next_try"] = 0.0
remote_launcher._menu_upkeep(settings, state, wx)
check("retry sends one reminder", len(wx.sent) == 1 and "@personEmail" in wx.sent[0])
check("day marked on disk", _DISK.get("okbase_remind_sent_day") == TODAY)
state["next_try"] = 0.0
remote_launcher._menu_upkeep(settings, state, wx)
check("no second reminder the same day", len(wx.sent) == 1 and calls["check"] == 2)

# A fresh listener (restart) still respects the day on disk.
state = {"day": "", "next_try": 0.0, "ok": None}
remote_launcher._menu_upkeep(settings, state, wx)
check("restart does not resend", len(wx.sent) == 1 and calls["check"] == 2)

# Ordered: nothing sent, day still marked.
_DISK.clear()
state = {"day": "", "next_try": 0.0, "ok": None}
answers[:] = [("ordered", "")]
wx = FakeWebex()
remote_launcher._menu_upkeep(settings, state, wx)
check("ordered sends nothing", wx.sent == [])
check("ordered marks the day", _DISK.get("okbase_remind_sent_day") == TODAY)

print()
print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
