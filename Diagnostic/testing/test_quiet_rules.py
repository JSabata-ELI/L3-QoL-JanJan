"""Quiet hours, channel mutes and mute lengths — the pure half, no Qt.

What decides whether an alert goes out at 3 a.m. lives in bot_commands so it
can be checked here on its own:

* every spelling of a quiet rule the chat (/quiet add) and the Settings table
  accept, and the ones they refuse;
* whether a rule is in force at a given moment — across midnight, by weekday,
  with a length instead of an end, with the rule or the master switch off;
* what each kind of rule holds back: "warnings" lets alarms (and the all-clear
  after an alarm) through;
* how long a /snooze or /channels mute lasts (`2h`, `until 8:00`, …).

Run with:  python testing/test_quiet_rules.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:                       # replies carry → and · ; a cp1250 console would
    sys.stdout.reconfigure(encoding="utf-8")   # die on a failure print
except Exception:          # noqa: BLE001
    pass

import bot_commands as bc  # noqa: E402

TZ = bc.TZ_DEFAULT
NS = bc.NS
OK, WARN, ALARM = bc.LEVEL_OK, bc.LEVEL_WARNING, bc.LEVEL_ALARM


def _ns(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp() * NS)


# 2026-10-23 is a Friday.
FRI_23 = _ns(2026, 10, 23, 23, 0)
SAT_03 = _ns(2026, 10, 24, 3, 0)
SAT_07 = _ns(2026, 10, 24, 7, 0)
SAT_23 = _ns(2026, 10, 24, 23, 0)
MON_03 = _ns(2026, 10, 26, 3, 0)
TUE_12 = _ns(2026, 10, 20, 12, 30)


def _rule(*segs):
    return bc.parse_quiet_rule(list(segs))


def _refused(*segs):
    try:
        bc.parse_quiet_rule(list(segs))
    except bc.CommandError as e:
        return str(e)
    raise AssertionError(f"{segs} should have been refused")


# ---------------------------------------------------------------------------
# Reading a rule
# ---------------------------------------------------------------------------

def test_the_documented_example():
    r = _rule("email", "mon-fri", "22:00-06:00", "warnings")
    assert r["channels"] == ["email"]
    assert r["days"] == [0, 1, 2, 3, 4]
    assert (r["start"], r["end"], r["duration_min"]) == ("22:00", "06:00",
                                                          None)
    assert r["hold"] == "warnings" and r["on"] is True


def test_the_order_of_the_segments_does_not_matter():
    a = _rule("email", "mon-fri", "22:00-06:00", "warnings")
    b = _rule("warnings", "22-6", "mon-fri", "email")
    assert a == b


def test_several_channels_and_all():
    assert _rule("webex, email", "all day")["channels"] == ["webex", "email"]
    assert _rule("all", "all day")["channels"] == list(bc.CHANNELS)
    # A later bare "all" is what is held, not the channels.
    r = _rule("email", "all day", "all")
    assert r["channels"] == ["email"] and r["hold"] == "all"


def test_days_in_every_spelling():
    for text, days in (("weekend", [5, 6]), ("sat,sun", [5, 6]),
                       ("sat sun", [5, 6]), ("weekdays", [0, 1, 2, 3, 4]),
                       ("daily", list(range(7))), ("fri-mon", [0, 4, 5, 6]),
                       ("po-pá", [0, 1, 2, 3, 4]), ("ne", [6])):
        assert bc.parse_days(text) == days, text
    assert bc.parse_days("email") is None


def test_days_default_to_every_day():
    assert _rule("email", "22:00-06:00")["days"] == list(range(7))


def test_a_start_and_a_length():
    r = _rule("email", "12:00", "1h", "label Lunch")
    assert (r["start"], r["end"], r["duration_min"]) == ("12:00", "", 60.0)
    assert r["label"] == "Lunch"


def test_all_day():
    r = _rule("webex", "weekend", "all day")
    assert (r["start"], r["end"]) == ("00:00", "24:00")


def test_what_is_refused():
    assert "channel" in _refused("mon-fri", "22:00-06:00")
    assert "when" in _refused("email", "mon-fri").lower()
    assert "until when" in _refused("email", "22:00").lower()
    assert "same time" in _refused("email", "8:00-8:00")
    assert "not both" in _refused("email", "8:00-9:00", "3h")
    assert "banana" in _refused("email", "22-6", "banana")


def test_days_text_reads_back():
    assert bc.days_text([0, 1, 2, 3, 4]) == "mon-fri"
    assert bc.days_text([5, 6]) == "sat-sun"
    assert bc.days_text(range(7)) == "daily"
    assert bc.days_text([0, 2, 4]) == "mon,wed,fri"


def test_the_one_line_description():
    text = bc.quiet_rule_text(_rule("email", "mon-fri", "22:00-06:00",
                                    "warnings", "label Night"))
    assert "**Night**" in text and "mon-fri 22:00-06:00" in text
    assert "alarms pass" in text
    assert "all day" in bc.quiet_rule_text(_rule("webex", "all day"))
    assert "for 1 h" in bc.quiet_rule_text(_rule("email", "12:00", "1h"))


# ---------------------------------------------------------------------------
# When a rule is in force
# ---------------------------------------------------------------------------

def test_a_window_past_midnight_belongs_to_its_first_day():
    night = _rule("email", "mon-fri", "22:00-06:00")
    assert bc.quiet_rule_active(night, FRI_23, TZ)       # Friday night
    assert bc.quiet_rule_active(night, SAT_03, TZ)       # …into Saturday
    assert not bc.quiet_rule_active(night, SAT_07, TZ)   # over at 6
    assert not bc.quiet_rule_active(night, SAT_23, TZ)   # no Saturday night
    # Monday 3 a.m. belongs to Sunday night, which is not in mon-fri.
    assert not bc.quiet_rule_active(night, MON_03, TZ)


def test_a_rule_with_a_length():
    lunch = _rule("email", "12:00", "1h")
    assert bc.quiet_rule_active(lunch, TUE_12, TZ)
    assert not bc.quiet_rule_active(lunch, _ns(2026, 10, 20, 13, 0), TZ)


def test_a_long_window_reaches_into_later_days():
    weekend = _rule("email", "fri", "18:00", "2d")
    assert bc.quiet_rule_active(weekend, SAT_23, TZ)
    assert not bc.quiet_rule_active(weekend, MON_03, TZ)


def test_a_rule_switched_off_never_applies():
    r = _rule("email", "all day")
    r["on"] = False
    assert not bc.quiet_rule_active(r, TUE_12, TZ)


# ---------------------------------------------------------------------------
# What a rule holds back
# ---------------------------------------------------------------------------

def test_warnings_lets_alarms_and_their_all_clear_through():
    assert bc.hold_applies("warnings", WARN, OK, "transition")
    assert bc.hold_applies("warnings", WARN, WARN, "reminder")
    assert not bc.hold_applies("warnings", ALARM, OK, "transition")
    assert not bc.hold_applies("warnings", ALARM, ALARM, "reminder")
    assert not bc.hold_applies("warnings", OK, ALARM, "transition")
    assert bc.hold_applies("warnings", OK, WARN, "transition")


def test_reminders_holds_only_reminders():
    assert bc.hold_applies("reminders", ALARM, ALARM, "reminder")
    assert not bc.hold_applies("reminders", WARN, OK, "transition")


def test_all_holds_everything():
    assert bc.hold_applies("all", ALARM, OK, "transition")


def _settings(**kw):
    s = {"channel_off_until": {}, "quiet_rules_enabled": True,
         "quiet_rules": []}
    s.update(kw)
    return s


def test_the_channel_verdict():
    s = _settings(quiet_rules=[_rule("email", "mon-fri", "22:00-06:00",
                                     "warnings", "label Night")])
    why = bc.channel_hold_reason(s, "email", WARN, OK, "transition",
                                 FRI_23, TZ)
    assert why == "quiet: Night"
    assert bc.channel_hold_reason(s, "email", ALARM, OK, "transition",
                                  FRI_23, TZ) == ""
    assert bc.channel_hold_reason(s, "webex", WARN, OK, "transition",
                                  FRI_23, TZ) == ""
    assert bc.channel_hold_reason(s, "email", WARN, OK, "transition",
                                  TUE_12, TZ) == ""


def test_the_master_switch_suspends_every_rule():
    s = _settings(quiet_rules=[_rule("email", "all day")],
                  quiet_rules_enabled=False)
    assert bc.channel_hold_reason(s, "email", WARN, OK, "transition",
                                  TUE_12, TZ) == ""


def test_a_manual_mute_holds_everything_until_it_ends():
    s = _settings(channel_off_until={"email": TUE_12 + 3600 * NS})
    why = bc.channel_hold_reason(s, "email", ALARM, OK, "transition",
                                 TUE_12, TZ)
    assert why.startswith("email muted until 13:30")
    assert bc.channel_hold_reason(s, "email", ALARM, OK, "transition",
                                  TUE_12 + 7200 * NS, TZ) == ""
    s = _settings(channel_off_until={"webex": bc.FOREVER})
    assert "switched back on" in bc.channel_hold_reason(
        s, "webex", ALARM, OK, "transition", TUE_12, TZ)


# ---------------------------------------------------------------------------
# How long a mute lasts
# ---------------------------------------------------------------------------

def _until(spec, now=TUE_12):
    return bc.parse_until(spec, now, TZ)


def test_lengths():
    assert _until("2h") == TUE_12 + 7200 * NS
    assert _until("30m") == TUE_12 + 1800 * NS
    assert _until("1,5 h") == TUE_12 + 5400 * NS
    assert _until("2d") == TUE_12 + 2 * 86400 * NS


def test_a_clock_time_means_the_next_time_it_comes():
    assert _until("until 18:00") == _ns(2026, 10, 20, 18, 0)
    assert _until("8:00") == _ns(2026, 10, 21, 8, 0)      # already past today
    assert _until("until tomorrow 7:30") == _ns(2026, 10, 21, 7, 30)
    assert _until("until tomorrow") == _ns(2026, 10, 21, 8, 0)
    assert _until("until 23.10. 8:00") == _ns(2026, 10, 23, 8, 0)


def test_forever():
    assert _until("forever") == bc.FOREVER
    assert bc.mute_live(bc.FOREVER, TUE_12)
    assert bc.until_text(bc.FOREVER, TUE_12) == "until switched back on"


def test_what_a_length_refuses():
    for bad in ("", "banana", "0h", "until 25:00", "until 1.1.2020 8:00"):
        try:
            _until(bad)
        except bc.CommandError:
            continue
        raise AssertionError(f"{bad!r} should have been refused")


def test_until_text():
    assert bc.until_text(_ns(2026, 10, 20, 18, 0), TUE_12, TZ) == \
        "until 18:00"
    assert bc.until_text(_ns(2026, 10, 21, 8, 0), TUE_12, TZ) == \
        "until tomorrow 08:00"
    assert bc.until_text(_ns(2026, 10, 23, 8, 0), TUE_12, TZ) == \
        "until Fri 23.10. 08:00"


def test_mute_live():
    assert not bc.mute_live(None, TUE_12)
    assert not bc.mute_live(TUE_12 - 1, TUE_12)
    assert bc.mute_live(TUE_12 + 1, TUE_12)


# ---------------------------------------------------------------------------
# Pacing and yes/no
# ---------------------------------------------------------------------------

def test_pacing():
    assert bc.parse_pacing("reminder 60, recovery off") == [
        ("renotify_cooldown_minutes", 60), ("recovery_notify", False)]
    assert bc.parse_pacing("hold 15") == [("rule_change_grace_minutes", 15.0)]
    for bad in ("reminder", "reminder 5000", "banana 3", "recovery maybe"):
        try:
            bc.parse_pacing(bad)
        except bc.CommandError:
            continue
        raise AssertionError(f"{bad!r} should have been refused")


def test_yes_and_no():
    assert bc.is_yes("ano") is True and bc.is_yes("Yes.") is True
    assert bc.is_yes("ne") is False and bc.is_yes("no") is False
    # An "ok" in the conversation must never confirm a removal.
    for word in ("ok", "a", "y", "6 15", "hello"):
        assert bc.is_yes(word) is None, word


def test_channels():
    assert bc.parse_channels("mail and webex") == ["webex", "email"]
    try:
        bc.parse_channels("sms")
    except bc.CommandError as e:
        assert "sms" in str(e)
    else:
        raise AssertionError("sms is not a channel")


# ---------------------------------------------------------------------------

def _run():
    fails = 0
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:      # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {e!r}")
    print("---")
    print(f"{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run())
