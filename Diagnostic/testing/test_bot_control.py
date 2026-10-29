"""The bot commands that run notifying from the chat.

Covered here, on a stand-in for MonitorWidget the way test_change_limits.py
does it (the real methods bound onto a SimpleNamespace, the network and the
window stubbed out):

* the PV list: /add (exact name, numbered pick, already monitored), /remove
  (asks first, `ano` removes, `ne` keeps), /rename;
* rules: /rule … gate / add / cond / label / move / remove, and the refusals
  that leave nothing half-done;
* alerting: /snooze (and the one alert owed when it ends with the PV still
  bad), /unsnooze, /ack (spent when the level moves), /channels, /quiet,
  /pacing;
* the undo stack across all of them, and /history on disk.

Run with:  python testing/test_bot_control.py
"""

import os
import sys
import tempfile
import types
from collections import deque
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:                       # replies carry →, ⚠ and emoji; a cp1250 console
    sys.stdout.reconfigure(encoding="utf-8")   # would die on a failure print
except Exception:          # noqa: BLE001
    pass

from PySide6.QtWidgets import QApplication  # noqa: E402

import bot_commands as bc  # noqa: E402
import monitor_tab as mt  # noqa: E402
from alerting import AlertLevel, Notification  # noqa: E402

SEC = 1_000_000_000
MIN = 60 * SEC
# Tuesday 2026-10-20 12:30 Prague.
NOW = int(datetime(2026, 10, 20, 12, 30, tzinfo=bc.TZ_DEFAULT).timestamp()) \
    * SEC
ME = "jan.moucka@eli-laser.eu"

_APP = QApplication.instance() or QApplication([])
_TMP = Path(tempfile.mkdtemp(prefix="diag_bot_test_"))
mt.HISTORY_FILE = _TMP / "bot_history.jsonl"

CHANNELS = ["L3-UTIL-CHL03-001:Temp", "L3-UTIL-CHL03-002:Temp",
            "L3-UTIL-CHL03-007:Temp", "L3-UTIL-CHL03-007:Flow",
            "L3-TIMING-TIMING:SysRate", "L3-SIS-KEY:HighPowerStatus"]


def _pvs():
    return [mt.PVConfig(name="L3-UTIL-CHL03-001:Temp",
                        display_name="DA1 Chiller", units="DegC",
                        enabled=True, warn_low=7.5, warn_high=24.0,
                        alarm_low=7.0, alarm_high=25.0),
            mt.PVConfig(name="L3-UTIL-CHL03-002:Temp",
                        display_name="DA2 Chiller", units="DegC",
                        enabled=True, warn_high=24.0, alarm_high=25.0,
                        gate_pvs=["L3-UTIL-CHL03-001:Temp"])]


def _clock(ns=NOW):
    mt.api.now_ns = lambda: ns


_clock()


def _win(pvs=None):
    pvs = list(pvs if pvs is not None else _pvs())
    calls = []
    win = types.SimpleNamespace(
        settings=dict(mt.DEFAULT_SETTINGS),
        pvs=pvs,
        runtime={p.name: mt.PVRuntime(current_value=20.0) for p in pvs},
        shared_ok=True, replies=[], logged=[], saved=[], calls=calls,
        _change_pending={}, _undo_stack=[], _history=deque(maxlen=50),
        _history_disk_lines=0, _gate_values={}, _all_channels=list(CHANNELS),
        _chart_jobs=[], _monitoring=True, _resume_at_ns=0,
        hub=types.SimpleNamespace(
            webex=types.SimpleNamespace(bot_name="Diagnostics"),
            active_channels=lambda: ["webex", "email"]),
        model=types.SimpleNamespace(refresh_all=lambda: None,
                                    reset=lambda: calls.append("reset")),
        graph=types.SimpleNamespace(
            refresh_combo=lambda: calls.append("combo"),
            refresh_data=lambda: None, redraw=lambda: None),
        evaluator=None,
    )
    win.settings["channel_off_until"] = {}
    win.settings["quiet_rules"] = []
    win._reply = types.MethodType(lambda s, m: s.replies.append(m), win)
    win._log = types.MethodType(lambda s, m: s.logged.append(m), win)
    win.persist = types.MethodType(
        lambda s: s.saved.append([p.to_dict() for p in s.pvs]), win)
    win._cancel_jobs = types.MethodType(lambda s: [], win)
    win._gate_value = types.MethodType(lambda s, n: None, win)
    win._new_history = types.MethodType(lambda s: deque(), win)
    win._backfill_one = types.MethodType(
        lambda s, pv: s.calls.append(("backfill", pv.name)), win)
    win._update_status = types.MethodType(lambda s: None, win)
    win._apply_pv_edit = types.MethodType(
        lambda s, pv: s.calls.append(("rules", pv.name)), win)
    win._prefetch_channels = types.MethodType(
        lambda s: s.calls.append("prefetch"), win)
    win._dispatch_alert = types.MethodType(
        lambda s, pv, rt, note: s.calls.append(("alert", pv.name, note)), win)
    for name in (
            "_find_pv", "_find_pvs", "_resolve_pvs", "_match_profile",
            "_is_change_answer", "_answer_change", "_cmd_cancel",
            "_cmd_undo", "_push_undo", "_pv_named", "_after_pv_change",
            "_settings_changed", "_recluster", "_bot_tag", "_drop_pv",
            "_cmd_add", "_answer_add", "_add_named", "_cmd_remove",
            "_answer_remove", "_cmd_rename", "_rules_listing", "_cmd_rules",
            "_rule_index", "_cmd_rule", "_rule_edit", "_split_pvs_and_time",
            "_cmd_snooze", "_cmd_unsnooze", "_cmd_ack", "_cmd_history",
            "_channel_lines", "_cmd_channels", "_quiet_listing", "_cmd_quiet",
            "_pacing_lines", "_cmd_pacing", "_cmd_settings", "_cmd_enable",
            "_eval_config", "_pv_hold_reason", "_hold_alert",
            "_expire_snooze", "_settle_snooze", "_channel_plan",
            "_history_add", "_history_load"):
        setattr(win, name, types.MethodType(getattr(mt.MonitorWidget, name),
                                            win))
    for name in ("_limits_label", "_pv_snapshot", "_add_picks", "_bot_cond",
                 "_quiet_index"):
        setattr(win, name, getattr(mt.MonitorWidget, name))
    win._HISTORY_ICON = mt.MonitorWidget._HISTORY_ICON
    return win


def _say(win, line, email=ME):
    """One chat line through the real command parser, as the bot gets it."""
    pc = bc.parse_command(line)
    handler = {
        "/add": win._cmd_add, "/remove": win._cmd_remove,
        "/rename": win._cmd_rename, "/rules": win._cmd_rules,
        "/rule": win._cmd_rule, "/snooze": win._cmd_snooze,
        "/unsnooze": win._cmd_unsnooze, "/ack": win._cmd_ack,
        "/history": win._cmd_history, "/channels": win._cmd_channels,
        "/quiet": win._cmd_quiet, "/pacing": win._cmd_pacing,
        "/settings": win._cmd_settings,
    }.get(pc.cmd)
    if pc.cmd == "/undo":
        win._cmd_undo(email)
    elif pc.cmd == "/cancel":
        win._cmd_cancel(email)
    elif pc.cmd in ("/enable", "/disable"):
        win._cmd_enable(pc, email, pc.cmd == "/enable")
    else:
        try:
            handler(pc, email)
        except bc.CommandError as e:      # as _handle_command answers it
            win._reply(f"⚠ {e} Try /help.")
    return win.replies[-1]


def _names(win):
    return [p.name for p in win.pvs]


# ---------------------------------------------------------------------------
# /add
# ---------------------------------------------------------------------------

def test_an_exact_archive_name_is_added_at_once():
    win = _win()
    reply = _say(win, "/add L3-UTIL-CHL03-007:Flow")
    assert "L3-UTIL-CHL03-007:Flow" in _names(win)
    pv = win.pvs[-1]
    assert pv.enabled is False and not pv.thresholds().is_active()
    assert "alerting is off" in reply and "/change" in reply
    assert ("backfill", pv.name) in win.calls and "reset" in win.calls
    assert win.saved


def test_several_matches_are_numbered_and_a_number_picks():
    win = _win()
    reply = _say(win, "/add chl03 007")
    assert "`1`" in reply and "`2`" in reply
    assert len(win.pvs) == 2                       # nothing added yet
    assert win._is_change_answer("2", ME)
    win._answer_change("2", ME)
    assert win.pvs[-1].name in ("L3-UTIL-CHL03-007:Temp",
                                "L3-UTIL-CHL03-007:Flow")
    assert win._change_pending == {}


def test_two_picks_at_once_and_as_a_command():
    win = _win()
    _say(win, "/add chl03 007")
    _say(win, "/add 1, 2")
    assert {"L3-UTIL-CHL03-007:Temp", "L3-UTIL-CHL03-007:Flow"} <= \
        set(_names(win))


def test_the_pick_list_marks_what_is_already_monitored():
    reply = _say(_win(), "/add chl03 temp")
    line = [ln for ln in reply.splitlines() if "CHL03-001" in ln][0]
    assert "already monitored" in line


def test_adding_what_is_already_there_says_so():
    win = _win()
    assert "Already monitored" in _say(win, "/add L3-UTIL-CHL03-001:Temp")
    assert len(win.pvs) == 2


def test_nothing_matching_is_an_error():
    assert "No archive PV matches" in _say(_win(), "/add sbw4 energy")


def test_no_channel_list_yet_asks_for_it():
    win = _win()
    win._all_channels = []
    assert "not loaded yet" in _say(win, "/add chl03")
    assert "prefetch" in win.calls


def test_undo_takes_an_added_pv_out_again():
    win = _win()
    _say(win, "/add L3-UTIL-CHL03-007:Flow")
    _say(win, "/undo")
    assert "L3-UTIL-CHL03-007:Flow" not in _names(win)
    assert "Undone" in win.replies[-1]


# ---------------------------------------------------------------------------
# /remove
# ---------------------------------------------------------------------------

def test_remove_asks_first():
    win = _win()
    reply = _say(win, "/remove DA1")
    assert len(win.pvs) == 2
    assert "ano" in reply
    # DA1 is DA2's dependency PV — the question says so.
    assert "dependency PV of DA2 Chiller" in reply


def test_ano_removes_and_undo_brings_it_back_whole():
    win = _win()
    win.pvs[0].profiles = [{"label": "x", "conds": [], "warn_high": 3.0}]
    _say(win, "/remove DA1")
    win._answer_change("ano", ME)
    assert _names(win) == ["L3-UTIL-CHL03-002:Temp"]
    assert "L3-UTIL-CHL03-001:Temp" not in win.runtime
    _say(win, "/undo")
    assert _names(win)[0] == "L3-UTIL-CHL03-001:Temp"      # same place
    assert win.pvs[0].profiles[0]["warn_high"] == 3.0     # rules and all
    assert win.pvs[0].warn_high == 24.0
    assert "L3-UTIL-CHL03-001:Temp" in win.runtime


def test_ne_keeps_it():
    win = _win()
    _say(win, "/remove DA1")
    win._answer_change("ne", ME)
    assert len(win.pvs) == 2 and "Kept" in win.replies[-1]


def test_ok_is_not_a_yes():
    win = _win()
    _say(win, "/remove DA1")
    assert not win._is_change_answer("ok", ME)
    assert win._is_change_answer("ano", ME)


def test_the_confirmation_can_come_as_a_command():
    win = _win()
    _say(win, "/remove DA2")
    _say(win, "/remove ano")
    assert _names(win) == ["L3-UTIL-CHL03-001:Temp"]


def test_a_remove_question_is_not_a_limits_answer():
    win = _win()
    _say(win, "/remove DA1")
    assert not win._is_change_answer("ano", ME, kinds=("limits",))


# ---------------------------------------------------------------------------
# /rename
# ---------------------------------------------------------------------------

def test_rename_and_undo():
    win = _win()
    _say(win, "/rename DA1; Main Chiller")
    assert win.pvs[0].display_name == "Main Chiller"
    assert "combo" in win.calls
    _say(win, "/undo")
    assert win.pvs[0].display_name == "DA1 Chiller"


def test_a_name_another_pv_has_is_refused():
    win = _win()
    assert "already the name" in _say(win, "/rename DA1; da2 chiller")
    assert win.pvs[0].display_name == "DA1 Chiller"


def test_rename_needs_the_separator():
    assert "/rename <pv>; <new name>" in _say(_win(), "/rename DA1 Main")


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------

def test_a_rule_built_step_by_step():
    win = _win()
    pv = win.pvs[0]
    _say(win, "/rule DA1; gate 1 L3-TIMING-TIMING:SysRate")
    assert pv.gate_pvs == ["L3-TIMING-TIMING:SysRate"]
    _say(win, "/rule DA1; add 3,3 Hz; 2.3")
    assert pv.profiles[-1]["label"] == "3,3 Hz"
    assert pv.profiles[-1]["conds"] == [[2.3, 2.3]]
    assert "no limits yet" in win.replies[-1]
    _say(win, "/rule DA1; gate 2 L3-SIS-KEY:HighPowerStatus")
    assert pv.profiles[-1]["conds"] == [[2.3, 2.3], [None, None]]
    _say(win, "/rule DA1; cond 1; 0.2-3; 1")
    assert pv.profiles[0]["conds"] == [[0.2, 3.0], [1.0, 1.0]]
    _say(win, "/rule DA1; label 1 Running")
    assert pv.profiles[0]["label"] == "Running"
    assert ("rules", pv.name) in win.calls


def test_clearing_a_gate_drops_its_conditions():
    win = _win()
    pv = win.pvs[0]
    pv.gate_pvs = ["A", "B"]
    pv.profiles = [{"label": "r", "conds": [[1, 1], [2, 2]]}]
    _say(win, "/rule DA1; gate 1 none")
    assert pv.gate_pvs == ["B"]
    assert pv.profiles[0]["conds"] == [[2, 2]]


def test_move_and_remove():
    win = _win()
    pv = win.pvs[0]
    pv.profiles = [{"label": "a"}, {"label": "b"}, {"label": "c"}]
    _say(win, "/rule DA1; move 3 1")
    assert [p["label"] for p in pv.profiles] == ["c", "a", "b"]
    _say(win, "/rule DA1; remove 2")
    assert [p["label"] for p in pv.profiles] == ["c", "b"]
    _say(win, "/undo")
    assert [p["label"] for p in pv.profiles] == ["c", "a", "b"]


def test_a_refused_rule_edit_leaves_nothing_behind():
    win = _win()
    pv = win.pvs[0]
    before = pv.to_dict()
    for line in ("/rule DA1; add x; 1",           # no gate for a condition
                 "/rule DA1; gate 1 NOT:A:PV",     # not an archive name
                 "/rule DA1; remove 1",            # no rules
                 "/rule DA1; cond 1; banana",
                 "/rule DA1; gate 1 L3-UTIL-CHL03-001:Temp",   # itself
                 "/rule DA1; frobnicate 2"):
        assert _say(win, line).startswith("⚠"), line
    assert pv.to_dict() == before
    assert not win._undo_stack


def test_rules_listing():
    win = _win()
    pv = win.pvs[0]
    pv.gate_pvs = ["L3-TIMING-TIMING:SysRate"]
    pv.profiles = [{"label": "3,3 Hz", "conds": [[2.3, 2.3]],
                    "warn_high": 14.4, "alarm_high": 14.7}]
    reply = _say(win, "/rules DA1")
    assert "Dependency 1: `L3-TIMING-TIMING:SysRate`" in reply
    assert "`1` **3,3 Hz**" in reply and "14.4" in reply
    assert "Global" in reply


# ---------------------------------------------------------------------------
# /snooze, /unsnooze, /ack
# ---------------------------------------------------------------------------

def _note(level=AlertLevel.WARNING, prev=AlertLevel.OK, kind="transition"):
    return Notification(level=level, prev_level=prev, value=30.0,
                        reason="x", kind=kind)


def test_snooze_holds_the_alert_and_owes_one_when_it_ends():
    win = _win()
    _say(win, "/snooze DA1; 2h")
    pv, rt = win.pvs[0], win.runtime[win.pvs[0].name]
    assert pv.snoozed_by == ME and bc.mute_live(pv.snoozed_until, NOW)
    why = win._pv_hold_reason(pv, rt, _note(), NOW)
    assert why.startswith("snoozed until 14:30")
    win._hold_alert(pv, rt, _note(), why)
    assert rt.snooze_owed
    assert win._history[-1]["kind"] == "held"
    # Two hours later the snooze is over and the PV is still in warning.
    later = NOW + 121 * MIN
    rt.alert.level = AlertLevel.WARNING
    win._expire_snooze(pv, rt, later)
    assert pv.snoozed_until is None
    win._settle_snooze(pv, rt, 30.0, later)
    kind, name, note = win.calls[-1]
    assert kind == "alert" and "Snooze over" in note.reason
    assert not rt.snooze_owed


def test_a_snooze_that_ends_after_recovery_sends_nothing():
    win = _win()
    pv, rt = win.pvs[0], win.runtime[win.pvs[0].name]
    rt.snooze_owed = True
    rt.alert.level = AlertLevel.OK
    win._settle_snooze(pv, rt, 20.0, NOW)
    assert not any(c[0] == "alert" for c in win.calls
                   if isinstance(c, tuple))


def test_snooze_without_a_separator_and_a_group():
    win = _win()
    reply = _say(win, "/snooze chiller until 18:00")
    assert all(bc.mute_live(p.snoozed_until, NOW) for p in win.pvs)
    assert "until 18:00" in reply and "DA1 Chiller, DA2 Chiller" in reply


def test_snooze_without_a_time_asks_for_one():
    assert "For how long" in _say(_win(), "/snooze DA1")


def test_snooze_survives_a_save_and_undo_ends_it():
    win = _win()
    _say(win, "/snooze DA1; forever")
    assert win.saved[-1][0]["snoozed_until"] == bc.FOREVER
    _say(win, "/undo")
    assert win.pvs[0].snoozed_until is None


def test_unsnooze_and_the_listing():
    win = _win()
    _say(win, "/snooze DA1, DA2; 1h")
    assert "DA1 Chiller" in _say(win, "/snooze")
    _say(win, "/unsnooze DA1")
    assert win.pvs[0].snoozed_until is None
    assert bc.mute_live(win.pvs[1].snoozed_until, NOW)
    _say(win, "/unsnooze all")
    assert win.pvs[1].snoozed_until is None


def test_ack_holds_reminders_of_that_level_only():
    win = _win()
    pv, rt = win.pvs[0], win.runtime[win.pvs[0].name]
    rt.alert.level = AlertLevel.WARNING
    reply = _say(win, "/ack")
    assert "DA1 Chiller (warning)" in reply
    assert rt.acked_level == AlertLevel.WARNING and rt.acked_by == ME
    rem = _note(AlertLevel.WARNING, AlertLevel.WARNING, "reminder")
    assert win._pv_hold_reason(pv, rt, rem, NOW).startswith("acknowledged")
    worse = _note(AlertLevel.ALARM, AlertLevel.WARNING, "transition")
    assert win._pv_hold_reason(pv, rt, worse, NOW) == ""


def test_ack_with_nothing_alarming():
    assert "Nothing to acknowledge" in _say(_win(), "/ack")


# ---------------------------------------------------------------------------
# /channels, /quiet, /pacing, /settings
# ---------------------------------------------------------------------------

def test_mute_a_channel_and_back():
    win = _win()
    reply = _say(win, "/channels email off 4h")
    assert "email muted until 16:30" in reply
    send, held = win._channel_plan(_payload())
    assert send == ["webex"] and "email" in held
    _say(win, "/channels email on")
    assert win._channel_plan(_payload())[0] == ["webex", "email"]


def test_muting_every_channel_is_said_loudly():
    reply = _say(_win(), "/channels all off")
    assert "Every channel is muted" in reply


def test_a_mute_is_undone():
    win = _win()
    _say(win, "/channels webex off 1h")
    _say(win, "/undo")
    assert win.settings["channel_off_until"] == {}


def test_the_mute_does_not_touch_the_defaults():
    """settings starts as a shallow copy of DEFAULT_SETTINGS — writing into
    the shared dict would mute every later copy too."""
    win = _win()
    win.settings["channel_off_until"] = mt.DEFAULT_SETTINGS[
        "channel_off_until"]
    _say(win, "/channels email off 1h")
    assert mt.DEFAULT_SETTINGS["channel_off_until"] == {}


def _payload(level=AlertLevel.WARNING, prev=AlertLevel.OK, kind="transition"):
    return mt.AlertPayload(level=level, prev_level=prev, pv_name="x",
                           display_name="X", value=1.0, units="",
                           reason="r", timestamp_str="", kind=kind)


def test_quiet_rules_from_the_chat():
    win = _win()
    reply = _say(win, "/quiet add email; daily; 12:00-13:00; warnings")
    assert "quiet rule 1 added" in reply
    assert "in force now" in reply                      # 12:30
    send, held = win._channel_plan(_payload())
    assert send == ["webex"] and held["email"] == "quiet: rule 1"
    # An alarm still goes.
    assert win._channel_plan(_payload(AlertLevel.ALARM))[0] == \
        ["webex", "email"]
    _say(win, "/quiet off 1")
    assert win._channel_plan(_payload())[0] == ["webex", "email"]
    _say(win, "/quiet on 1")
    _say(win, "/quiet off")
    assert win.settings["quiet_rules_enabled"] is False
    assert win._channel_plan(_payload())[0] == ["webex", "email"]
    _say(win, "/undo")
    assert win.settings["quiet_rules_enabled"] is True
    _say(win, "/quiet remove 1")
    assert win.settings["quiet_rules"] == []


def test_a_bad_quiet_rule_changes_nothing():
    win = _win()
    assert _say(win, "/quiet add mon-fri; 22-6").startswith("⚠")
    assert _say(win, "/quiet remove 3").startswith("⚠")
    assert win.settings["quiet_rules"] == [] and not win._undo_stack


def test_pacing_changes_rebuild_the_evaluator():
    win = _win()
    _say(win, "/pacing reminder 60, recovery off")
    assert win.settings["renotify_cooldown_minutes"] == 60
    assert win.settings["recovery_notify"] is False
    assert win.evaluator is not None
    assert win.evaluator.config.renotify_cooldown_minutes == 60
    _say(win, "/undo")
    assert win.settings["renotify_cooldown_minutes"] == \
        mt.DEFAULT_SETTINGS["renotify_cooldown_minutes"]
    assert "Re-notify cooldown" in _say(win, "/pacing")


def test_settings_overview():
    win = _win()
    _say(win, "/snooze DA2; 1h")
    _say(win, "/channels email off 1h")
    reply = _say(win, "/settings")
    for must in ("MONITORING", "email", "muted", "Re-notify cooldown",
                 "snoozed: DA2 Chiller"):
        assert must in reply, must


def test_enable_is_undoable():
    win = _win()
    _say(win, "/disable DA1")
    assert win.pvs[0].enabled is False
    _say(win, "/undo")
    assert win.pvs[0].enabled is True


# ---------------------------------------------------------------------------
# The undo stack across commands, and /history
# ---------------------------------------------------------------------------

def test_undo_walks_back_through_different_commands():
    win = _win()
    _say(win, "/add L3-UTIL-CHL03-007:Flow")
    _say(win, "/snooze DA1; 1h")
    _say(win, "/rename DA2; Second")
    _say(win, "/undo")
    assert win._pv_named("L3-UTIL-CHL03-002:Temp").display_name == \
        "DA2 Chiller"
    _say(win, "/undo")
    assert win.pvs[0].snoozed_until is None
    _say(win, "/undo")
    assert "L3-UTIL-CHL03-007:Flow" not in _names(win)
    assert "last step" in win.replies[-1]


def test_history_is_written_and_read_back():
    if mt.HISTORY_FILE.exists():
        mt.HISTORY_FILE.unlink()
    win = _win()
    _say(win, "/snooze DA1; 1h")
    _say(win, "/channels email off 1h")
    assert mt.HISTORY_FILE.exists()
    fresh = _win()                         # a restart
    fresh._history_load()
    assert len(fresh._history) == 2
    reply = _say(fresh, "/history")
    assert "DA1 Chiller" in reply and "jan.moucka" in reply
    assert "DA1" in _say(fresh, "/history DA1")
    assert "Nothing in the history" in _say(fresh, "/history DA2")


def test_history_trims_itself():
    if mt.HISTORY_FILE.exists():
        mt.HISTORY_FILE.unlink()
    win = _win()
    win._history = deque(maxlen=mt.HISTORY_KEEP)
    for i in range(mt.HISTORY_TRIM_AT + 10):
        win._history_add("change", f"row {i}")
    lines = mt.HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= mt.HISTORY_TRIM_AT
    assert lines[-1].endswith('"row %d"}' % (mt.HISTORY_TRIM_AT + 9))


def test_cancel_drops_any_question():
    win = _win()
    _say(win, "/add chl03 007")
    _say(win, "/cancel")
    assert win._change_pending == {}
    assert "nothing changed" in win.replies[-1]


# ---------------------------------------------------------------------------

def _run():
    fails = 0
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    for name, fn in tests:
        _clock()
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:      # noqa: BLE001
            fails += 1
            import traceback
            print(f"FAIL {name}: {e!r}")
            traceback.print_exc()
    print("---")
    print(f"{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run())
