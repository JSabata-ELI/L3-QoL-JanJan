"""Tests for moving a PV's limits from the Webex bot — `/change` and `/undo`.

`/change` is the only command that asks a question and waits for the answer, and
the only one that WRITES a monitoring rule. Both halves are covered here:

* the pure grammar in bot_commands — the numbered listing, and every spelling of
  an answer the operator might type. No Qt, no network;
* the command itself, rendered offline on a stand-in object carrying only the
  few attributes the replies read, the way test_pv_filter.py and
  test_rule_change_grace.py do it.

The one thing worth saying about the numbering: it is derived from the
configuration, but the rows are HANDED to the session rather than recomputed
when the answer lands, so an edit made in the window meanwhile cannot silently
retarget a number.

Run with:  python testing/test_change_limits.py
"""

import os
import sys
import types
from collections import deque

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:                       # the replies carry →, ⚠ and ← ; a cp1250 console
    sys.stdout.reconfigure(encoding="utf-8")   # would die on a failure print
except Exception:          # noqa: BLE001
    pass

from PySide6.QtWidgets import QApplication  # noqa: E402

import bot_commands as bc  # noqa: E402
import monitor_tab as mt  # noqa: E402
from alerting import AlertLevel, AlertState  # noqa: E402

SEC = 1_000_000_000
MIN = 60 * SEC
NOW = 1_000_000 * SEC
ME = "jan.moucka@eli-beams.eu"

_APP = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Fixtures — the real DA1 chiller, rules and all, plus a plain PV
# ---------------------------------------------------------------------------

def _da1():
    return mt.PVConfig(
        name="L3-UTIL-CHL03-001:Temp", display_name="DA1 Chiller",
        units="DegC", enabled=True,
        warn_low=7.5, warn_high=24.0, alarm_low=7.0, alarm_high=25.0,
        valid_min=0.0, valid_max=50.0,
        gate_pvs=["L3-SIS-KEY:HighPowerStatus", "L3-TIMING-TIMING:SysRate"],
        profiles=[
            {"label": "0,2 Hz", "conds": [[1.0, 1.0], [0.2, 0.2]],
             "warn_low": 15.8, "warn_high": 16.8,
             "alarm_low": 15.5, "alarm_high": 17.1},
            {"label": "3,3 Hz", "conds": [[1.0, 1.0], [2.3, 2.3]],
             "warn_low": 13.8, "warn_high": 14.4,
             "alarm_low": 13.5, "alarm_high": 14.7},
        ])


def _pvs():
    return [_da1(),
            mt.PVConfig(name="L3-UTIL-CHL03-002:Temp",
                        display_name="DA2 Chiller", units="DegC",
                        warn_low=7.5, warn_high=24.0,
                        alarm_low=7.0, alarm_high=25.0),
            mt.PVConfig(name="L3-UTIL-DAQ-PresVRTPL1:Press",
                        display_name="PLFE VRT1", warn_high=0.01,
                        alarm_high=0.025),
            mt.PVConfig(name="L3-UTIL-DAQ-PresVRTPL2:Press",
                        display_name="PLFE VRT2", warn_high=0.01,
                        alarm_high=0.025)]


def _rt(value, age_s=30):
    rt = mt.PVRuntime(current_value=value,
                      history=deque([(NOW - i * MIN, value) for i in range(5)]),
                      last_update_ns=NOW - int(age_s) * SEC,
                      data_ts_ns=NOW - int(age_s) * SEC)
    rt.live_level = AlertLevel.OK
    rt.alert.level = AlertLevel.OK
    return rt


def _win(pvs=None, value=16.2, shared_ok=True, saved=None):
    """Stand-in for MonitorWidget carrying what /change touches."""
    pvs = list(pvs if pvs is not None else _pvs())
    saved = saved if saved is not None else []
    win = types.SimpleNamespace(
        settings=dict(mt.DEFAULT_SETTINGS),
        pvs=pvs,
        runtime={p.name: _rt(value) for p in pvs},
        shared_ok=shared_ok,
        replies=[],
        logged=[],
        saved=saved,
        _change_pending={},
        _change_undo=None,
        _chart_jobs=[],
        hub=types.SimpleNamespace(
            webex=types.SimpleNamespace(bot_name="Diagnostics")),
        model=types.SimpleNamespace(refresh_all=lambda: None),
    )
    win._reply = types.MethodType(
        lambda self, m: self.replies.append(m), win)
    win._log = types.MethodType(lambda self, m: self.logged.append(m), win)
    win.persist = types.MethodType(
        lambda self: self.saved.append([p.to_dict() for p in self.pvs]), win)
    win._cancel_jobs = types.MethodType(lambda self: [], win)
    # The gates are not being read here, so no profile matches and Global is
    # "in force" — which is the honest answer for a stand-in with no poll.
    win._gate_value = types.MethodType(lambda self, name: None, win)
    for name in ("_find_pv", "_find_pvs", "_match_profile",
                 "_active_thresholds", "_is_change_answer", "_answer_change",
                 "_cmd_change", "_change_listing", "_limit_scope_values",
                 "_apply_limit_changes", "_limit_warnings", "_change_done",
                 "_cmd_undo", "_cmd_cancel", "_strip_mention",
                 "_change_in_one_line"):
        setattr(win, name, types.MethodType(getattr(mt.MonitorWidget, name),
                                            win))
    for name in ("_limit_get", "_limit_set", "_limits_label"):  # staticmethods
        setattr(win, name, getattr(mt.MonitorWidget, name))
    return win


def _clock(ns=NOW):
    mt.api.now_ns = lambda: ns


_clock()


def _change(win, args, email=ME):
    win._cmd_change(bc.parse_command(f"/change {args}"), email)
    return win.replies[-1]


# ---------------------------------------------------------------------------
# The numbered listing
# ---------------------------------------------------------------------------

def test_global_is_numbered_first_then_every_rule():
    rows = bc.limit_rows(_da1())
    assert [r.n for r in rows] == list(range(1, 13))
    assert [(r.scope, r.label) for r in rows[:4]] == [
        ("Global", "alarm low"), ("Global", "warn low"),
        ("Global", "warn high"), ("Global", "alarm high")]
    assert rows[5].scope == "0,2 Hz" and rows[5].field == "warn_low"
    assert rows[5].value == 15.8
    assert rows[10].scope == "3,3 Hz" and rows[10].field == "warn_high"


def test_a_pv_with_no_rules_has_exactly_four():
    rows = bc.limit_rows(mt.PVConfig(name="x", display_name="PLFE VRT1",
                                     warn_high=0.01))
    assert len(rows) == 4
    assert rows[1].value is None          # warn low was never set


def test_the_listing_marks_the_rule_in_force_and_the_current_value():
    win = _win()
    text = _change(win, "DA1")
    assert "DA1 Chiller" in text and "L3-UTIL-CHL03-001:Temp" in text
    assert "16.2" in text                 # the value now, so a typo shows up
    assert "in force now" in text
    assert "`6` warn low **15.8**" in text
    assert "`12` alarm high **14.7**" in text


def test_the_listing_spells_out_the_tag():
    """In a room a bare answer never reaches the bot, so the reply must say so."""
    text = _change(_win(), "DA1")
    assert "@Diagnostics 3 24" in text      # Global warn high, its own value
    assert "@Diagnostics /change 3 24" in text


def test_the_worked_example_names_a_number_that_exists():
    """A PV with no rules stops at 4, so a hard-coded "6 15" would be a lie."""
    text = _change(_win(), "PLFE VRT1")
    shown = [int(ln.split("`")[1]) for ln in text.splitlines()
             if ln.startswith("`") and ln.split("`")[1].isdigit()]
    assert shown == [1, 2, 3, 4]
    example = [ln for ln in text.splitlines() if "Answer" in ln][0]
    assert "@Diagnostics 3 " in example


def test_a_limit_that_is_not_set_reads_as_not_set():
    win = _win()
    text = _change(win, "PLFE VRT1")
    assert "`2` warn low **not set**" in text


# ---------------------------------------------------------------------------
# Every way of saying the same thing
# ---------------------------------------------------------------------------

def _answers(text):
    return bc.parse_change_answer(text, bc.limit_rows(_da1()))


def test_the_plain_form_and_all_its_paddings():
    for spelling in ("6 15", "limit 6 value 15", "limit 6 hodnota 15",
                     "6 = 15", "  6   15  ", "limit 6 na 15"):
        (row, value), = _answers(spelling)
        assert (row.n, row.scope, row.field, value) == \
            (6, "0,2 Hz", "warn_low", 15.0), spelling


def test_a_decimal_comma_is_a_decimal_point():
    (_, value), = _answers("6 15,5")
    assert value == 15.5


def test_several_limits_in_one_line():
    got = _answers("6 15, 7 17")
    assert [(r.n, v) for r, v in got] == [(6, 15.0), (7, 17.0)]


def test_a_limit_can_be_named_instead_of_numbered():
    (row, value), = _answers("warn high 25")
    assert (row.n, row.scope, row.field, value) == \
        (3, "Global", "warn_high", 25.0)
    (row, value), = _answers("wh 25")
    assert row.n == 3


def test_a_rule_can_be_named_even_though_its_label_holds_a_comma():
    """"3,3 Hz" must not be cut in half by the comma that separates items."""
    (row, value), = _answers("3,3 Hz warn high 14.6")
    assert (row.n, row.scope, value) == (11, "3,3 Hz", 14.6)
    (row, value), = _answers("3,3 warn high 14,6")
    assert (row.n, value) == (11, 14.6)


def test_none_clears_a_limit():
    (row, value), = _answers("2 none")
    assert row.n == 2 and value is None


def test_a_number_outside_the_list_is_refused_by_name():
    try:
        _answers("99 15")
    except bc.CommandError as e:
        assert "1 to 12" in str(e)
    else:
        raise AssertionError("99 should not be a limit")


def test_ordinary_conversation_is_not_an_answer():
    rows = bc.limit_rows(_da1())
    for line in ("hello there", "ok", "yes", "6", "thanks 5 people",
                 "and then the chiller went to 15 degrees", "warn 15",
                 "99 15", "brb"):
        assert not bc.looks_like_answer(line, rows), line


# ---------------------------------------------------------------------------
# Applying the change
# ---------------------------------------------------------------------------

def test_the_answer_moves_the_number_and_saves_it():
    win = _win()
    _change(win, "DA1")
    win._answer_change("6 16", ME)
    assert win.pvs[0].profiles[0]["warn_low"] == 16.0
    assert win.saved, "the change was never persisted"
    assert win.saved[-1][0]["profiles"][0]["warn_low"] == 16.0
    reply = win.replies[-1]
    assert "15.8" in reply and "16" in reply and "/undo" in reply
    assert "next reading" in reply


def test_the_question_is_forgotten_once_it_is_answered():
    win = _win()
    _change(win, "DA1")
    win._answer_change("6 16", ME)
    assert win._change_pending == {}
    assert not win._is_change_answer("7 17", ME)


def test_one_line_without_the_listing():
    win = _win()
    _change(win, "DA1 warn high 25")
    assert win.pvs[0].warn_high == 25.0
    assert win._change_pending == {}      # nothing was left open


def test_the_answer_may_come_back_as_a_command():
    """In a room this is the form that always gets through."""
    win = _win()
    _change(win, "DA1")
    _change(win, "6 16")
    assert win.pvs[0].profiles[0]["warn_low"] == 16.0


def test_a_change_only_reaches_the_pv_it_was_asked_about():
    win = _win()
    _change(win, "DA1")
    win._answer_change("6 16", ME)
    assert win.pvs[1].warn_low == 7.5     # DA2 untouched
    assert win.pvs[0].warn_low == 7.5     # DA1's Global untouched


def test_two_people_can_be_changing_different_pvs_at_once():
    win, other = _win(), "kolega@eli-beams.eu"
    _change(win, "DA1")
    _change(win, "DA2", email=other)
    win._answer_change("3 23", other)
    assert win.pvs[1].warn_high == 23.0
    assert win.pvs[0].warn_high == 24.0   # the other question still open
    assert win._is_change_answer("3 23", ME)


def test_clearing_a_limit_says_it_raises_nothing():
    win = _win()
    _change(win, "DA1")
    win._answer_change("4 none", ME)
    assert win.pvs[0].alarm_high is None
    assert "raises nothing at all" in win.replies[-1]


# ---------------------------------------------------------------------------
# What the window's own editor does not check
# ---------------------------------------------------------------------------

def test_a_value_that_crosses_another_bound_is_refused():
    win = _win()
    _change(win, "DA1")
    win._answer_change("4 5", ME)          # alarm high under warn high 24
    assert win.pvs[0].alarm_high == 25.0   # nothing written
    assert not win.saved
    assert "Nothing changed" in win.replies[-1]


def test_a_refusal_judges_the_whole_answer_at_once():
    """Two bounds in one line are legal together or not at all."""
    win = _win()
    _change(win, "DA1")
    win._answer_change("3 30, 4 28", ME)   # warn high 30 over alarm high 28
    assert win.pvs[0].warn_high == 24.0 and win.pvs[0].alarm_high == 25.0
    win._answer_change("3 26, 4 28", ME)   # the same pair, in order
    assert win.pvs[0].warn_high == 26.0 and win.pvs[0].alarm_high == 28.0


def test_a_limit_outside_the_valid_range_is_flagged_but_applied():
    win = _win()
    _change(win, "DA1")
    win._answer_change("4 90", ME)         # valid_max is 50
    assert win.pvs[0].alarm_high == 90.0
    assert "valid range" in win.replies[-1]


def test_a_value_already_the_wrong_side_is_said_out_loud():
    win = _win(value=30.0)
    _change(win, "DA1")
    win._answer_change("3 25", ME)
    assert "already" in win.replies[-1]


def test_a_change_that_stays_on_this_pc_says_so():
    win = _win(shared_ok=False)
    _change(win, "DA1")
    win._answer_change("6 16", ME)
    assert "this PC only" in win.replies[-1]


# ---------------------------------------------------------------------------
# One PV at a time
# ---------------------------------------------------------------------------

def test_a_fragment_fitting_several_pvs_lists_them_and_opens_nothing():
    win = _win()
    text = _change(win, "plfe")
    assert "PLFE VRT1" in text and "PLFE VRT2" in text
    assert win._change_pending == {}


def test_a_fragment_fitting_nothing_is_an_error():
    win = _win()
    assert "no PV matches" in _change(win, "sbw4")


def test_change_with_no_argument_explains_itself():
    win = _win()
    assert "/change <pv>" in _change(win, "")


def test_change_with_no_argument_reprints_an_open_listing():
    win = _win()
    _change(win, "DA1")
    assert "`6` warn low" in _change(win, "")


# ---------------------------------------------------------------------------
# Forgetting
# ---------------------------------------------------------------------------

def test_a_stale_question_is_not_answered():
    win = _win()
    _change(win, "DA1")
    _clock(NOW + (mt.CHANGE_SESSION_TTL_S + 60) * SEC)
    try:
        assert not win._is_change_answer("6 15", ME)
        win._answer_change("6 16", ME)
        assert win.pvs[0].profiles[0]["warn_low"] == 15.8   # untouched
        assert "forgotten" in win.replies[-1]
    finally:
        _clock()


def test_a_bare_number_from_somebody_who_asked_nothing_is_ignored():
    win = _win()
    assert not win._is_change_answer("6 15", "nekdo@eli-beams.eu")


def test_cancel_drops_the_question():
    win = _win()
    _change(win, "DA1")
    win._cmd_cancel(ME)
    assert win._change_pending == {}
    assert "nothing changed" in win.replies[-1].lower()
    assert not win._is_change_answer("6 15", ME)


def test_the_tag_comes_off_a_bare_answer():
    win = _win()
    assert win._strip_mention("Diagnostics 6 15") == "6 15"
    assert win._strip_mention("@Diagnostics 6 15") == "6 15"
    assert win._strip_mention("6 15") == "6 15"
    assert win._strip_mention("Diagnostic 6 15") == "6 15"


# ---------------------------------------------------------------------------
# /undo
# ---------------------------------------------------------------------------

def test_undo_puts_the_number_back():
    win = _win()
    _change(win, "DA1")
    win._answer_change("6 16", ME)
    win._cmd_undo(ME)
    assert win.pvs[0].profiles[0]["warn_low"] == 15.8
    assert len(win.saved) == 2            # saved again, not just in memory
    assert win.saved[-1][0]["profiles"][0]["warn_low"] == 15.8


def test_undo_restores_every_limit_of_one_answer():
    win = _win()
    _change(win, "DA1")
    win._answer_change("6 16, 7 17", ME)
    win._cmd_undo(ME)
    assert win.pvs[0].profiles[0]["warn_low"] == 15.8
    assert win.pvs[0].profiles[0]["warn_high"] == 16.8


def test_undo_is_one_step_only():
    win = _win()
    _change(win, "DA1 warn high 22")
    _change(win, "DA1 warn high 23")
    win._cmd_undo(ME)
    assert win.pvs[0].warn_high == 22.0
    win._cmd_undo(ME)
    assert "Nothing to undo" in win.replies[-1]
    assert win.pvs[0].warn_high == 22.0


def test_undo_with_nothing_behind_it():
    win = _win()
    win._cmd_undo(ME)
    assert "Nothing to undo" in win.replies[-1]
    assert not win.saved


def test_anybody_can_undo_anybody_else_s_change():
    win = _win()
    _change(win, "DA1 warn high 22")
    win._cmd_undo("kolega@eli-beams.eu")
    assert win.pvs[0].warn_high == 24.0


# ---------------------------------------------------------------------------
# What the change does to alerting — the grace comes for free
# ---------------------------------------------------------------------------

def _grace_win(pv):
    """The slice of MonitorWidget that _check_limits_change runs on."""
    win = types.SimpleNamespace(settings=dict(mt.DEFAULT_SETTINGS), logged=[])
    win._log = types.MethodType(lambda self, m: self.logged.append(m), win)
    win._check_limits_change = types.MethodType(
        mt.MonitorWidget._check_limits_change, win)
    win._limits_key = mt.MonitorWidget._limits_key
    win._limits_label = mt.MonitorWidget._limits_label
    return win


def test_a_bot_change_opens_the_same_hold_a_rule_switch_does():
    win = _win()
    _change(win, "DA1")
    pv = win.pvs[0]
    grace, rt = _grace_win(pv), _rt(24.5)
    grace._check_limits_change(pv, rt, NOW)      # first pass: baseline
    assert rt.grace_until_ns == 0
    win._answer_change("3 20", ME)               # Global warn high 24 → 20
    grace._check_limits_change(pv, rt, NOW + MIN)
    assert rt.in_grace(NOW + MIN)
    assert "held until" in grace.logged[-1]


def test_an_alarm_nobody_was_told_about_is_cleared_by_the_change():
    win = _win()
    _change(win, "DA1")
    pv = win.pvs[0]
    grace, rt = _grace_win(pv), _rt(24.5)
    rt.alert = AlertState(level=AlertLevel.WARNING, since_ns=NOW)
    grace._check_limits_change(pv, rt, NOW)
    win._answer_change("3 20", ME)
    grace._check_limits_change(pv, rt, NOW + MIN)
    assert rt.alert.level == AlertLevel.OK


# ---------------------------------------------------------------------------

def _run():
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:      # noqa: BLE001
            fails += 1
            print(f"FAIL {name}: {e}")
    total = sum(1 for n in globals() if n.startswith("test_"))
    print("---")
    print(f"{total - fails}/{total} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run())
