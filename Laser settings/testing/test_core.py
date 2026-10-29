"""Laser settings — the rules, without a screen.

    python testing/test_core.py

No Qt and no archiver: every check here is about what a number MEANS.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ls_core as C                                             # noqa: E402

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


class FakeReading:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    @property
    def has_value(self):
        return self.error is None and self.value is not None


def one_value(row, reading, *, now_s=1000.0, last_ok_s=1000.0):
    """One row's `RowValue`, the way both tabs work it out."""
    pv = row.get("pv") or ""
    readings = {} if reading is None else {pv: reading}
    last_ok = {} if last_ok_s is None else {pv: last_ok_s}
    return C.row_values([row], readings, last_ok, now_s)[int(row["id"])]


def test_limits_are_inclusive():
    print("the limits themselves count as in range")
    check("below", C.judge(0.9, 1, 10)[0], C.STATE_BAD)
    check("on the minimum", C.judge(1, 1, 10)[0], C.STATE_OK)
    check("in the middle", C.judge(5, 1, 10)[0], C.STATE_OK)
    check("on the maximum", C.judge(10, 1, 10)[0], C.STATE_OK)
    check("above", C.judge(10.1, 1, 10)[0], C.STATE_BAD)


def test_an_empty_box_is_not_zero():
    print("an empty limit box switches that side off, it never means zero")
    check("no lower limit, well below zero",
          C.judge(-500, None, 10)[0], C.STATE_OK)
    check("no upper limit, huge", C.judge(1e9, 1, None)[0], C.STATE_OK)
    check("no upper limit, still below the lower one",
          C.judge(0, 1, None)[0], C.STATE_BAD)
    check("neither limit set is not a pass",
          C.judge(5, None, None)[0], C.STATE_UNKNOWN)


def test_nothing_read_is_never_a_tick():
    print("a missing reading is never shown as in range")
    row = C.new_row(1, "PV:A", "A")
    C.limits_for(row, "E2").update({"min": 1.0, "max": 2.0})
    check("never read",
          C.judge_row(row, one_value(row, None, last_ok_s=None), ["E2"])[0],
          C.STATE_UNKNOWN)
    check("read failed",
          C.judge_row(row, one_value(row, FakeReading(error="HTTP 500")),
                      ["E2"])[0],
          C.STATE_UNKNOWN)
    check("nothing archived",
          C.judge_row(row, one_value(row, FakeReading(value=None)), ["E2"])[0],
          C.STATE_UNKNOWN)
    check("read long ago",
          C.judge_row(row, one_value(row, FakeReading(value=1.5),
                                     last_ok_s=1000.0 - C.STALE_AFTER_S - 1),
                      ["E2"])[0],
          C.STATE_UNKNOWN)
    check("read just now",
          C.judge_row(row, one_value(row, FakeReading(value=1.5),
                                     last_ok_s=999.0), ["E2"])[0],
          C.STATE_OK)


def test_no_hall_means_no_verdict():
    print("with no hall being shot into nothing is judged")
    row = C.new_row(1, "PV:A", "A")
    C.limits_for(row, "E2").update({"min": 1.0, "max": 2.0})
    state, text = C.judge_row(row, one_value(row, FakeReading(value=99.0)), [])
    check("state", state, C.STATE_UNKNOWN)
    check("sentence", text, "no hall is being shot into")


def test_each_hall_has_its_own_limits():
    print("the same channel, different limits per hall")
    row = C.new_row(1, "PV:A", "A")
    C.limits_for(row, "E2").update({"min": 10.0, "max": 20.0})
    C.limits_for(row, "E4").update({"min": 30.0, "max": 40.0})
    value = one_value(row, FakeReading(value=15.0))
    check("fine in E2", C.judge_row(row, value, ["E2"])[0], C.STATE_OK)
    check("wrong in E4", C.judge_row(row, value, ["E4"])[0], C.STATE_BAD)


def test_a_channel_added_is_added_in_every_hall():
    print("one list of channels: added in one hall, watched in all five")
    row = C.new_row(7, "PV:A", "Delay", "ns")
    check("watched", C.is_shown(row), True)
    for hall in C.HALLS:
        entry = C.limits_for(row, hall)
        check(f"{hall} has no limits of its own yet",
              (entry["min"], entry["max"]), (None, None))
    print("what belongs to one hall alone is Min and Max, and nothing else")
    C.limits_for(row, "E2").update({"min": 1.0, "max": 2.0})
    check("E4's minimum is untouched", C.limits_for(row, "E4")["min"], None)
    print("Show is one switch, not one per hall")
    check("no per-hall show is written", "show" in C.limits_for(row, "E2"),
          False)
    row["show"] = False
    check("off everywhere at once", C.is_shown(row), False)
    print("the unit is the channel's own, so it is the same everywhere")
    check("one unit on the row", row["unit"], "ns")


def test_a_stale_per_hall_show_is_stripped():
    print("a per-hall Show left in the file is thrown away, never believed")
    row = C.new_row(1, "PV:A", "A")
    row["limits"]["E2"]["show"] = False
    entry = C.limits_for(row, "E2")
    check("stripped on first touch", "show" in entry, False)
    check("and the row's own switch decides", C.is_shown(row), True)


def test_unticked_channel_cannot_blame_a_hall():
    print("a channel not ticked cannot make any hall blink")
    row = C.new_row(1, "PV:A", "A")
    C.limits_for(row, "E2").update({"min": 1.0, "max": 2.0})
    C.limits_for(row, "E4").update({"min": 1.0, "max": 2.0})
    row["show"] = False
    values = {1: C.RowValue(99.0, None, 100.0)}
    check("no fault raised in E2", C.hall_faults([row], values, "E2"), [])
    check("nor in E4", C.hall_faults([row], values, "E4"), [])
    check("and it is not judged either",
          C.judge_row(row, values[1], ["E2"]), (C.STATE_UNKNOWN, "not watched"))
    row["show"] = True
    check("fault raised once ticked",
          [r["id"] for r in C.hall_faults([row], values, "E2")], [1])


def test_alarm_off_keeps_the_row_but_never_blinks():
    print("Alarm unticked: shown, not judged, no hall blinks (a GDD scan)")
    row = C.new_row(1, "PV:GDD", "GDD", "fs^2")
    check("on for a new row", C.is_alarmed(row), True)
    C.limits_for(row, "E2").update({"min": 1.0, "max": 2.0})
    values = {1: C.RowValue(99.0, None, 100.0)}
    row["alarm"] = False
    check("still shown", C.is_shown(row), True)
    check("no fault raised", C.hall_faults([row], values, "E2"), [])
    state, text = C.judge_row(row, values[1], ["E2"])
    check("a grey dash", state, C.STATE_UNKNOWN)
    check("and the sentence says why and where it stands", text,
          "alarm off — above 2")
    check("kept through the settings file",
          C.is_alarmed(C.normalise({"rows": [row]})["rows"][0]), False)
    check("an old file without it alarms",
          C.is_alarmed(C.normalise({"rows": [{"id": 1, "pv": "PV:A"}]})
                       ["rows"][0]), True)
    row["alarm"] = True
    check("ticked again, the fault is back",
          [r["id"] for r in C.hall_faults([row], values, "E2")], [1])


def test_scanning_judges_on_the_scan_limits():
    print("the Overview's Scanning box: every row with scan limits switches")
    # The user's own example (12.10.2026): watched at 24800-25000, scanned at
    # 24000-26000; 24500 is green while scanning and a fault the moment the
    # box is unticked.
    row = C.new_row(1, "PV:Delay", "Front end delay", "ps")
    for hall in ("E2", "E4"):
        C.limits_for(row, hall).update({"min": 24800.0, "max": 25000.0})
    row.update({"scan_min": 24000.0, "scan_max": 26000.0})
    check("any row can carry scan limits", C.can_scan(row), True)
    check("off unless the box is ticked", C.is_scanning(row, False), False)
    inside = {1: C.RowValue(24500.0, None, 100.0)}
    outside = {1: C.RowValue(26500.0, None, 100.0)}
    check("scanning: 24500 is no fault in E2",
          C.hall_faults([row], inside, "E2", True), [])
    check("nor in E4", C.hall_faults([row], inside, "E4", True), [])
    state, text = C.judge_row(row, inside[1], ["E2"], True)
    check("a tick", state, C.STATE_OK)
    check("the sentence says scan", text, "scan: in 24000 … 26000")
    check("not scanning: 24500 faults the hall at once",
          [r["id"] for r in C.hall_faults([row], inside, "E2", False)], [1])
    check("out of the scan range faults the hall",
          [r["id"] for r in C.hall_faults([row], outside, "E2", True)], [1])
    check("the square knows a scan is on",
          [r["id"] for r in C.scanning_rows([row], True)], [1])
    check("and that it is not, with the box unticked",
          C.scanning_rows([row], False), [])
    row["alarm"] = False
    check("Alarm off still wins",
          C.hall_faults([row], outside, "E2", True), [])
    row["alarm"] = True
    row["show"] = False
    check("an unshown row is not a scan on the square",
          C.scanning_rows([row], True), [])
    row["show"] = True
    print("a row with no scan limits keeps its own while scanning")
    plain = C.new_row(2, "PV:E", "Energy", "J")
    C.limits_for(plain, "E2").update({"min": 1.0, "max": 2.0})
    check("not scanning itself", C.is_scanning(plain, True), False)
    check("judged on the hall's Min and Max",
          [r["id"] for r in C.hall_faults([plain],
                                          {2: C.RowValue(5.0, None, 1.0)},
                                          "E2", True)], [2])
    check("one scan side is enough",
          C.has_scan_limits({**plain, "scan_max": 3.0}), True)
    print("the settings file")
    back = C.normalise({"rows": [row], "scanning": True})
    check("scan limits kept",
          (back["rows"][0]["scan_min"], back["rows"][0]["scan_max"]),
          (24000.0, 26000.0))
    check("the switch kept", back["scanning"], True)
    check("off by default", C.normalise({"rows": []})["scanning"], False)
    old_tick = {"id": 1, "pv": "PV:GDD", "name": "GDD", "scan": True,
                "scan_min": 1.0, "scan_max": 2.0}
    check("an old per-row tick turns the switch on",
          C.normalise({"rows": [old_tick]})["scanning"], True)
    check("and is not carried on the row",
          "scan" in C.normalise({"rows": [old_tick]})["rows"][0], False)
    check("the shot counter cannot scan",
          C.can_scan(C.new_shot_row(9)), False)


def test_the_little_square_grey_vs_orange():
    print("grey = cannot be read, orange = read but not shooting")
    row = C.new_row(1, "PV:A", "A")
    C.limits_for(row, "Int").update({"min": 1.0, "max": 2.0})
    C.limits_for(row, "E2").update({"min": 1.0, "max": 2.0})
    fine = C.RowValue(1.5, None, 100.0)
    wrong = C.RowValue(9.0, None, 100.0)
    lost = C.RowValue(None, "HTTP 500", None)
    check("unreadable while shooting: grey",
          C.row_mark(row, lost, ["E2"], True, "E2")[0], C.STATE_UNKNOWN)
    check("unreadable while idle: grey",
          C.row_mark(row, lost, [], False, "Int")[0], C.STATE_UNKNOWN)
    check("and it says why",
          C.row_mark(row, lost, [], False, "Int")[1], "HTTP 500")
    check("shooting, in range: green",
          C.row_mark(row, fine, ["E2"], True, "E2")[0], C.STATE_OK)
    check("shooting, out of range: red",
          C.row_mark(row, wrong, ["E2"], True, "E2")[0], C.STATE_BAD)
    check("not firing, in range: orange tick",
          C.row_mark(row, fine, ["E2"], False, "E2")[0], C.MARK_IDLE_OK)
    check("not firing, out of range: orange cross",
          C.row_mark(row, wrong, ["E2"], False, "E2")[0], C.MARK_IDLE_BAD)
    check("no hall in force: judged against the idle hall",
          C.row_mark(row, wrong, [], True, "Int")[0], C.MARK_IDLE_BAD)
    check("the sentence says it is not being shot",
          C.row_mark(row, fine, [], False, "Int")[1].startswith(
              "not shooting"), True)
    bare = C.new_row(2, "PV:B", "B")
    check("no limits: white, nothing judged",
          C.row_mark(bare, fine, ["E2"], True, "E2")[0], C.MARK_NONE)
    row["alarm"] = False
    check("Alarm off: white",
          C.row_mark(row, wrong, ["E2"], True, "E2")[0], C.MARK_NONE)
    row["alarm"] = True
    row.update({"scan_min": 0.0, "scan_max": 10.0})
    check("idle and scanning: the scan pair",
          C.row_mark(row, wrong, [], False, "Int", True)[0], C.MARK_IDLE_OK)


def test_a_formula_may_use_a_row_below_it():
    print("formulas work in any order — the rows can be dragged")
    calc = C.new_formula_row(1, "Half", "A / 2")
    src = C.new_row(2, "PV:E", "E")
    rows = [calc, src]
    calc["expr"], calc["bind"] = C.expr_from_display("B / 2", rows)
    values = C.row_values(rows, {"PV:E": FakeReading(value=8.0)},
                          {"PV:E": 1000.0}, 1000.0)
    check("worked out from the row under it", values[1].value, 4.0)
    check("no complaint", C.formula_problems(calc, rows), [])
    check("list order kept", list(values), [1, 2])
    print("formulas on formulas, in any order")
    top = C.new_formula_row(3, "Quarter")
    rows = [top, calc, src]
    top["expr"], top["bind"] = C.expr_from_display("B / 2", rows)
    values = C.row_values(rows, {"PV:E": FakeReading(value=8.0)},
                          {"PV:E": 1000.0}, 1000.0)
    check("two steps", values[3].value, 2.0)
    print("a loop is said, not hung on")
    a = C.new_formula_row(4, "X")
    b = C.new_formula_row(5, "Y")
    loop = [a, b]
    a["expr"], a["bind"] = C.expr_from_display("B + 1", loop)
    b["expr"], b["bind"] = C.expr_from_display("A + 1", loop)
    values = C.row_values(loop, {}, {}, 1000.0)
    check("no number", (values[4].value, values[5].value), (None, None))
    check("the loop is named", "loop" in (values[4].problem or ""), True)


def test_a_stale_reading_cannot_raise_a_hall():
    print("a hall does not blink on a number nobody has refreshed")
    row = C.new_row(1, "PV:A", "A")
    C.limits_for(row, "E2").update({"min": 1.0, "max": 2.0})
    readings = {"PV:A": FakeReading(value=99.0)}
    last_ok = {"PV:A": 1000.0 - C.STALE_AFTER_S - 1}
    values = C.row_values([row], readings, last_ok, 1000.0)
    check("written off, not blamed", C.hall_faults([row], values, "E2"), [])
    check("and it says how long ago",
          "not read for" in (values[1].problem or ""), True)


def test_a_position_switch():
    print("what a position switch has to say")
    check("one", C.switch_made(1.0), True)
    check("nearly one", C.switch_made(0.9999), True)
    check("zero", C.switch_made(0.0), False)
    check("nothing", C.switch_made(None), False)
    check("two", C.switch_made(2.0), False)


def _gate_scene(open_value=None, closed_value=None, hall="Int"):
    """A settings document with one hall's valve reading what you say."""
    cfg = C.default_config()
    names = {"open": C.gate_for(cfg["shooting"], hall)["open"],
             "closed": C.closed_channel(hall)}
    readings, last_ok = {}, {}
    for key, value in (("open", open_value), ("closed", closed_value)):
        channel = names[key]
        if value is None:
            continue
        if isinstance(value, str):          # a failure, not a number
            readings[channel] = FakeReading(error=value)
            continue
        readings[channel] = FakeReading(value=value)
        last_ok[channel] = 1000.0
    return cfg, readings, last_ok


def test_the_open_switch_answers_on_its_own():
    print("one switch is the whole answer: not open means shut")
    cfg, readings, last_ok = _gate_scene(open_value=1.0, hall="E2")
    state, why = C.hall_state(cfg["shooting"], "E2", readings, last_ok, 1000.0)
    check("open means shooting into E2", state, C.STATE_SHOOTING)
    check("and it names the channel it read",
          "L3BT-MSS:SGV502_OPEN_IN" in why, True)
    cfg, readings, last_ok = _gate_scene(open_value=0.0, hall="E2")
    state, _why = C.hall_state(cfg["shooting"], "E2", readings, last_ok, 1000.0)
    check("not open means not shooting", state, C.STATE_IDLE)


def test_int_shoots_while_its_valve_is_shut():
    print("Int is internal shooting: shut valve means the beam goes there")
    check("Int's rule", C.shoot_when("Int"), C.CLOSED)
    check("E2's rule", C.shoot_when("E2"), C.OPEN)
    print("and it is not a setting — nothing in the file can change it")
    saved = C.gate_for(C.default_config()["shooting"], "Int")
    check("the gate entry holds one key", sorted(saved), ["open"])

    cfg, readings, last_ok = _gate_scene(open_value=0.0)
    state, _why = C.hall_state(cfg["shooting"], "Int", readings, last_ok, 1000.0)
    check("valve shut means shooting", state, C.STATE_SHOOTING)

    cfg, readings, last_ok = _gate_scene(open_value=1.0)
    state, _why = C.hall_state(cfg["shooting"], "Int", readings, last_ok, 1000.0)
    check("valve open means not shooting", state, C.STATE_IDLE)


def test_only_the_open_switch_is_read():
    print("one channel a valve, not two")
    cfg = C.default_config()
    # SGV501 publishes on the CPVA archiver, SGV502 to SGV505 only in the
    # L3BT archive behind the proxy.
    for hall, number in (("Int", 501), ("E2", 502), ("E3", 503),
                         ("E4", 504), ("E5", 505)):
        if number == 501:
            want_open = f"L3-PSS:SGV{number}_IN_OPEN_POSITION"
            want_closed = f"L3-PSS:SGV{number}_IN_CLOSED_POSITION"
        else:
            want_open = f"L3BT-MSS:SGV{number}_OPEN_IN"
            want_closed = f"L3BT-MSS:SGV{number}_CLOSED_IN"
        check(f"{hall} reads its open switch",
              C.gate_channels(cfg["shooting"], hall, readings={}, last_ok={},
                              now_s=1000.0),
              [want_open])
        check(f"{hall}'s fallback is in the program",
              C.closed_channel(hall), want_closed)
        check(f"{hall}'s names say which way round they are",
              (C.switch_sense(want_open), C.switch_sense(want_closed)),
              (C.OPEN, C.CLOSED))
    check("and nothing has been read yet either",
          C.gate_channels(cfg["shooting"], "Int"),
          ["L3-PSS:SGV501_IN_OPEN_POSITION"])


def test_the_closed_switch_is_only_a_fallback():
    print("the closed switch is asked for only while the open one is down")
    cfg = C.default_config()
    open_pv = C.gate_for(cfg["shooting"], "Int")["open"]
    closed_pv = C.closed_channel("Int")

    good = ({open_pv: FakeReading(value=1.0)}, {open_pv: 1000.0})
    check("a good open switch asks for nothing else",
          C.gate_channels(cfg["shooting"], "Int", readings=good[0],
                          last_ok=good[1], now_s=1000.0), [open_pv])

    failed = ({open_pv: FakeReading(error="HTTP 500")}, {})
    check("a failed one brings the fallback in",
          C.gate_channels(cfg["shooting"], "Int", readings=failed[0],
                          last_ok=failed[1], now_s=1000.0),
          [open_pv, closed_pv])

    check("so does one nobody has refreshed",
          C.gate_channels(cfg["shooting"], "Int", readings=good[0],
                          last_ok=good[1], now_s=1000.0 + C.STALE_AFTER_S + 1),
          [open_pv, closed_pv])

    check("and it drops out again when the open switch comes back",
          C.gate_channels(cfg["shooting"], "Int", readings=good[0],
                          last_ok=good[1], now_s=1000.0), [open_pv])

    print("and the fallback answers on its own while it is in")
    cfg, readings, last_ok = _gate_scene(open_value="HTTP 500",
                                         closed_value=1.0)
    state, why = C.hall_state(cfg["shooting"], "Int", readings, last_ok, 1000.0)
    check("shut, so Int is being shot into", state, C.STATE_SHOOTING)
    check("and the tooltip says where the answer came from",
          "the open switch could not be read" in why, True)

    cfg, readings, last_ok = _gate_scene(open_value="HTTP 500",
                                         closed_value="HTTP 500")
    state, _why = C.hall_state(cfg["shooting"], "Int", readings, last_ok, 1000.0)
    check("neither readable claims nothing", state, C.STATE_UNKNOWN)


def test_a_valve_nobody_has_touched_is_still_read():
    print("a stale READ is unknown; an old VALUE is not")
    cfg, readings, last_ok = _gate_scene(open_value=0.0)
    state, _why = C.hall_state(cfg["shooting"], "Int", readings, last_ok,
                               1000.0 + C.STALE_AFTER_S - 1)
    check("read a moment ago", state, C.STATE_SHOOTING)
    state, _why = C.hall_state(cfg["shooting"], "Int", readings, last_ok,
                               1000.0 + C.STALE_AFTER_S + 1)
    check("not read for too long", state, C.STATE_UNKNOWN)
    # The look-back has to reach a valve that has not moved in months.
    import ls_cpva
    check("the widest look-back is a year",
          ls_cpva.LastReader.STEPS_S[-1], 365 * 86400)


def test_the_old_settings_files_still_open():
    print("a settings file from before the valve had two channels")
    back = C.normalise({"shooting": {"Int": "OLD:PV"}})
    gate = C.gate_for(back["shooting"], "Int")
    check("the old name became the open switch", gate["open"], "OLD:PV")
    check("and nothing else is in there", sorted(gate), ["open"])

    print("a settings file that still holds the three dead settings")
    back = C.normalise({"shooting": {"E2": {"open": "HALL:E2:Open",
                                            "closed": "HALL:E2:Shut",
                                            "on": False,
                                            "shoot_when": "closed"}}})
    gate = C.gate_for(back["shooting"], "E2")
    check("the open name is kept", gate["open"], "HALL:E2:Open")
    check("the rest is dropped", sorted(gate), ["open"])
    check("E2 still shoots with the valve open", C.shoot_when("E2"), C.OPEN)

    print("a channel the operator cleared stays cleared")
    back = C.normalise({"shooting": {"Int": {"open": ""}}})
    check("not resurrected from the defaults",
          C.gate_for(back["shooting"], "Int")["open"], "")
    check("but the fallback still answers",
          C.gate_channels(back["shooting"], "Int", readings={}, last_ok={},
                          now_s=1000.0),
          ["L3-PSS:SGV501_IN_CLOSED_POSITION"])


def test_a_formula_is_worked_out_of_the_rows_above_it():
    print("a formula over the letters of the rows above it")
    src = C.new_row(1, "PV:SBW4", "SBW4 energy", "J")
    calc = C.new_formula_row(2, "SBW4 after the compressor", "A * 0.749", "J",
                             {"A": 1})
    rows = [src, calc]
    check("the letters", [C.row_letter(i) for i in range(3)], ["A", "B", "C"])
    values = C.row_values(rows, {"PV:SBW4": FakeReading(value=3.0)},
                          {"PV:SBW4": 1000.0}, 1000.0)
    check("worked out", round(values[2].value, 6), 2.247)
    check("nothing to complain about", values[2].problem, None)

    print("and it is judged against this hall's limits like any other row")
    C.limits_for(calc, "E2").update({"min": 3.0, "max": 4.0})
    check("out of range", C.judge_row(calc, values[2], ["E2"])[0], C.STATE_BAD)
    check("and it can make the hall blink",
          [r["id"] for r in C.hall_faults(rows, values, "E2")], [2])


def test_a_formula_with_nothing_under_it_is_a_dash():
    print("a computed number is never a tick while a source is a dash")
    src = C.new_row(1, "PV:SBW4", "SBW4 energy", "J")
    calc = C.new_formula_row(2, "compressed", "A * 0.749", "J", {"A": 1})
    rows = [src, calc]
    C.limits_for(calc, "E2").update({"min": 0.0, "max": 100.0})

    values = C.row_values(rows, {"PV:SBW4": FakeReading(error="HTTP 500")},
                          {}, 1000.0)
    check("the source failed", values[2].value, None)
    check("so the formula says which letter it was",
          values[2].problem.startswith("A:"), True)
    check("and it is not a tick",
          C.judge_row(calc, values[2], ["E2"])[0], C.STATE_UNKNOWN)

    print("a stale source makes the formula stale too")
    values = C.row_values(rows, {"PV:SBW4": FakeReading(value=3.0)},
                          {"PV:SBW4": 1000.0 - C.STALE_AFTER_S - 1}, 1000.0)
    check("no number", values[2].value, None)

    print("a formula that cannot work out to a number is a dash, not a crash")
    zero = C.new_row(3, "PV:Z", "zero")
    div = C.new_formula_row(4, "over zero", "A / C", "", {"A": 1, "C": 3})
    rows = [src, calc, zero, div]
    values = C.row_values(rows, {"PV:SBW4": FakeReading(value=3.0),
                                 "PV:Z": FakeReading(value=0.0)},
                          {"PV:SBW4": 1000.0, "PV:Z": 1000.0}, 1000.0)
    check("divided by zero", values[4].value, None)
    check("and says so", "did not work out" in values[4].problem, True)


def test_a_formula_that_cannot_be_read_back():
    print("everything wrong with a formula is said in words")
    src = C.new_row(1, "PV:A", "A")
    rows = [src]

    loose = C.new_formula_row(2, "loose", "A + Z", "", {"A": 1})
    rows.append(loose)
    check("a letter bound to nothing",
          C.formula_problems(loose, rows)[0],
          "Z is not one of the rows on the list.")

    itself = C.new_formula_row(3, "itself", "C * 2", "", {"C": 3})
    rows.append(itself)
    check("a row that uses itself",
          C.formula_problems(itself, rows)[0], "C is this row itself.")

    later = C.new_row(4, "PV:B", "B")
    ahead = C.new_formula_row(5, "ahead", "D + 1", "", {"D": 4})
    rows = [src, ahead, later]
    # Allowed since 12.10.2026 — the rows can be dragged into any order.
    check("a row further down the list is fine",
          C.formula_problems(ahead, rows), [])

    broken = C.new_formula_row(6, "broken", "A *", "", {"A": 1})
    rows = [src, broken]
    check("an expression that will not parse",
          C.formula_problems(broken, rows)[0].startswith(
              "The expression could not be read"), True)

    fine = C.new_formula_row(7, "fine", "A * 2", "", {"A": 1})
    check("a good one has nothing to say",
          C.formula_problems(fine, [src, fine]), [])


def test_a_formula_cannot_run_anything():
    print("a settings file must not be able to run code")
    row = C.new_formula_row(1, "nasty", "__import__('os').getcwd()", "", {})
    values = C.row_values([row], {}, {}, 1000.0)
    check("no number came out", values[1].value, None)
    check("and it was not allowed to try",
          "did not work out" in (values[1].problem or ""), True)


def test_the_letters_follow_the_rows():
    print("moving a row rewrites the formula box, not what it means")
    first = C.new_row(1, "PV:A", "A")
    second = C.new_row(2, "PV:B", "B")
    calc = C.new_formula_row(3, "twice B", "B * 2", "", {"B": 2})
    rows = [first, second, calc]
    check("as typed", C.expr_to_display(calc, rows), "B * 2")
    rows = [second, first, calc]            # the two channels swap places
    check("after the swap it is written in the new letter",
          C.expr_to_display(calc, rows), "A * 2")
    values = C.row_values(rows, {"PV:A": FakeReading(value=1.0),
                                 "PV:B": FakeReading(value=5.0)},
                          {"PV:A": 1000.0, "PV:B": 1000.0}, 1000.0)
    check("and it still means the same row", values[3].value, 10.0)

    print("what is typed is resolved to the rows there and then")
    expr, bind = C.expr_from_display("= B * 2", rows)
    check("the = is not part of it", expr, "B * 2")
    check("B is the second row on the list", bind, {"B": 1})


def test_limits_survive_a_save():
    print("the settings file keeps every limit exactly")
    cfg = C.default_config()
    C.gate_for(cfg["shooting"], "E2")["open"] = "HALL:E2:Open"
    row = C.new_row(1, "PV:A", "Delay", "ns")
    C.limits_for(row, "E2").update({"min": 10.0, "max": 20.5})
    C.limits_for(row, "E4").update({"min": None, "max": 3.0})
    row["show"] = False
    cfg["rows"].append(row)
    calc = C.new_formula_row(2, "half of it", "A / 2", "ns", {"A": 1})
    C.limits_for(calc, "E2").update({"min": 5.0, "max": 10.25})
    cfg["rows"].append(calc)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cfg.json"
        check("saved", C.save_config(cfg, path), None)
        back, problem = C.load_config(path)
    check("read back with no problem", problem, None)
    check("gate channel", C.gate_for(back["shooting"], "E2")["open"],
          "HALL:E2:Open")
    got = back["rows"][0]
    check("channel", got["pv"], "PV:A")
    check("unit", got["unit"], "ns")
    check("E2 min", C.limits_for(got, "E2")["min"], 10.0)
    check("E2 max", C.limits_for(got, "E2")["max"], 20.5)
    check("E4 min stays empty", C.limits_for(got, "E4")["min"], None)
    check("E4 max", C.limits_for(got, "E4")["max"], 3.0)
    check("unticked stays unticked", C.is_shown(got), False)
    kept = back["rows"][1]
    check("and a row saved without it is shown", C.is_shown(kept), True)
    check("the formula", kept["expr"], "A / 2")
    check("what it points at", kept["bind"], {"A": 1})
    check("its own limits", C.limits_for(kept, "E2")["max"], 10.25)


def test_a_broken_file_is_not_a_lost_file():
    print("one bad field does not throw the other forty away")
    cfg = {"rows": [{"id": 1, "pv": "PV:A", "name": "A",
                     "limits": {"E2": {"min": "not a number", "max": 5}}},
                    "this is not a row",
                    {"id": 1, "pv": "PV:B", "name": "B"},
                    {"id": 4, "name": "half", "expr": "A / 2",
                     "bind": {"A": 1, "B": "not an id"}}],
           "shooting": {"E2": 17, "E3": "PV:S",
                        "E4": {"open": 5, "shoot_when": "sideways"}},
           "active_hall": "nowhere"}
    back = C.normalise(cfg)
    # The last two rows are the shot counter and DM settings, which are always
    # put on the list.
    check("rows kept", [r["pv"] for r in back["rows"]],
          ["PV:A", "PV:B", "", "", ""])
    check("repeated id made unique",
          len({r["id"] for r in back["rows"]}), 5)
    check("bad limit emptied", C.limits_for(back["rows"][0], "E2")["min"], None)
    check("good limit kept", C.limits_for(back["rows"][0], "E2")["max"], 5.0)
    check("the formula survived", back["rows"][2]["expr"], "A / 2")
    check("a binding that is not an id is dropped",
          back["rows"][2]["bind"], {"A": 1})
    # A whole gate entry that is not readable at all is not an operator's
    # decision, it is damage — so that hall goes back to the default rather
    # than to nothing.
    check("an unreadable gate entry falls back to the default",
          C.gate_for(back["shooting"], "E2")["open"],
          "L3BT-MSS:SGV502_OPEN_IN")
    check("a real channel name is kept",
          C.gate_for(back["shooting"], "E3")["open"], "PV:S")
    check("a number is not a channel name",
          C.gate_for(back["shooting"], "E4")["open"], "")
    check("unknown hall falls back", back["active_hall"], C.HALLS[0])


def test_search_is_a_filter():
    print("a fragment that fits several channels returns those channels")
    names = ["L3-UTIL-CHL03-001:Temp", "L3-UTIL-CHL04-001:Temp",
             "L3-PULSER:Delay", "TEMP-CHL:Other"]
    matches, total = C.search_channels(names, "chl temp")
    check("both chillers", matches[:2],
          ["L3-UTIL-CHL03-001:Temp", "L3-UTIL-CHL04-001:Temp"])
    check("best match first", matches[0], "L3-UTIL-CHL03-001:Temp")
    check("total counted", total, 3)
    check("word order does not matter", C.search_channels(names, "temp chl"),
          (matches, total))
    check("nothing typed is not the whole list",
          C.search_channels(names, "")[0], [])


def test_a_comma_is_a_decimal_point():
    print("a Czech keyboard's comma")
    check("comma", C.parse_level("1,5"), 1.5)
    check("empty is not zero", C.parse_level("  "), None)
    check("nonsense is not zero", C.parse_level("abc"), None)
    check("empty box shows nothing", C.level_text(None), "")


def test_a_comma_in_a_formula():
    print("a decimal comma in a formula, without wrecking min and max")
    check("the plain case", C.fix_decimal_commas("A * 0,749"), "A * 0.749")
    check("spaces around it too", C.fix_decimal_commas("A * 0 , 749"),
          "A * 0.749")
    check("a comma inside brackets separates arguments",
          C.fix_decimal_commas("min(A, B)"), "min(A, B)")
    check("even between two numbers", C.fix_decimal_commas("min(1, 2)"),
          "min(1, 2)")
    check("round's digits are safe", C.fix_decimal_commas("round(A, 2)"),
          "round(A, 2)")
    check("both at once", C.fix_decimal_commas("min(A, 1) * 0,749"),
          "min(A, 1) * 0.749")
    check("nested", C.fix_decimal_commas("min(max(A,B), 1) * 0,749"),
          "min(max(A,B), 1) * 0.749")
    check("nothing is lost when there is no comma",
          C.fix_decimal_commas("(A + B) / 2"), "(A + B) / 2")

    print("so the typed comma reaches the file as a dot, and works out")
    rows = [C.new_row(1, "PV:A", "A")]
    calc = C.new_formula_row(2, "three quarters")
    rows.append(calc)
    expr, bind = C.expr_from_display("= A * 0,749", rows)
    check("stored with a dot", expr, "A * 0.749")
    calc["expr"], calc["bind"] = expr, bind
    values = C.row_values(rows, {"PV:A": FakeReading(value=100.0)},
                          {"PV:A": 1000.0}, 1000.0)
    check("and it is a number", values[2].value, 74.9)

    print("a correct min(1, 2) is never called wrong for its comma")
    calc["expr"], calc["bind"] = "min(A, 1) * 0.749", {"A": 1}
    check("nothing is reported", C.formula_problems(calc, rows), [])
    calc["expr"], calc["bind"] = "min(1, 2)", {}
    check("nor here", C.formula_problems(calc, rows), [])


def test_a_new_formula_row_stays_a_formula():
    print("a formula row is a formula before anything is typed into it")
    fresh = C.new_formula_row(9, name="new formula")
    check("nothing to work out yet", C.is_formula(fresh), False)
    check("but it IS a formula row", C.is_formula_row(fresh), True)
    check("a plain row is not", C.is_formula_row(C.new_row(1, "PV:A")), False)

    print("and the empty marker survives a save, or the next typing is a PV")
    cfg = C.default_config()
    cfg["rows"].append(fresh)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cfg.json"
        C.save_config(cfg, path)
        back, _problem = C.load_config(path)
    check("still a formula row", C.is_formula_row(back["rows"][0]), True)


def test_a_formula_typed_into_the_channel_box_is_repaired():
    print("a formula that landed in the channel box is put back")
    # The real shape, out of a settings file on 30.9.2026.
    cfg = {"rows": [
        {"id": 1, "pv": "L3-SPFE:Order2_RB", "name": "GDD"},
        {"id": 2, "pv": "L3-SPFE:Order3_RB", "name": "TOD"},
        {"id": 3, "pv": "HAPLS-ENER:Energy", "name": "SBW4"},
        {"id": 4, "pv": "C*0.749", "name": "SBW4 Compressed", "expr": "",
         "bind": {}},
    ]}
    back = C.normalise(cfg)
    hurt = back["rows"][3]
    check("the channel box is empty again", hurt["pv"], "")
    check("the formula is where it belongs", hurt["expr"], "C*0.749")
    check("C is the third row, which is SBW4", hurt["bind"], {"C": 3})
    check("and it works out", C.row_values(
        back["rows"], {"HAPLS-ENER:Energy": FakeReading(value=10.0)},
        {"HAPLS-ENER:Energy": 1000.0}, 1000.0)[4].value, 7.49)

    print("a real channel name on a formula row is left alone")
    cfg = {"rows": [{"id": 1, "pv": "L3-PSS:Thing", "expr": ""}]}
    check("a colon means it is a channel",
          C.normalise(cfg)["rows"][0]["pv"], "L3-PSS:Thing")


def test_int_is_watched_while_the_other_valves_are_silent():
    print("with E2-E5 unreadable, Int's limits are in force anyway")
    shooting = {h: C.default_gate(h) for h in C.HALLS}
    int_open = C.default_gate("Int")["open"]
    # Int's valve says OPEN, which for Int means NOT shooting. On the old rule
    # that was "no hall is being shot into" and nothing was ever judged.
    readings = {int_open: FakeReading(value=1.0)}
    last_ok = {int_open: 1000.0}
    halls, fallback = C.halls_in_force(shooting, readings, last_ok, 1000.0)
    check("Int is in force", halls, ["Int"])
    check("and it says it is the fallback", fallback, True)

    print("the rule switches itself off the moment one of them answers")
    e2_open = C.default_gate("E2")["open"]
    readings[e2_open] = FakeReading(value=0.0)
    last_ok[e2_open] = 1000.0
    halls, fallback = C.halls_in_force(shooting, readings, last_ok, 1000.0)
    check("nobody is shooting", halls, [])
    check("and no fallback", fallback, False)

    print("a hall really being shot into always wins")
    readings[e2_open] = FakeReading(value=1.0)
    halls, fallback = C.halls_in_force(shooting, readings, last_ok, 1000.0)
    check("E2 it is", halls, ["E2"])
    check("no fallback", fallback, False)

    print("a stale answer is not an answer")
    readings = {int_open: FakeReading(value=1.0),
                e2_open: FakeReading(value=0.0)}
    last_ok = {int_open: 1000.0, e2_open: 1000.0 - C.STALE_AFTER_S - 1}
    halls, fallback = C.halls_in_force(shooting, readings, last_ok, 1000.0)
    check("back to Int", (halls, fallback), (["Int"], True))


class FakeScreen:
    """An `ls_screen.ScreenRead`, without importing the Windows half."""
    def __init__(self, positions, at=1000.0, problem=None):
        self.positions = positions
        self.at = at
        self.problem = problem


def test_the_mss_window_answers_while_the_valves_are_silent():
    print("E2-E5 silent, the L3BT MSS window says SGV502 is open")
    shooting = {h: C.default_gate(h) for h in C.HALLS}
    int_open = C.default_gate("Int")["open"]
    readings = {int_open: FakeReading(value=1.0)}
    last_ok = {int_open: 1000.0}
    screen = FakeScreen({"E2": C.OPEN, "E3": C.CLOSED, "E4": C.CLOSED,
                         "E5": C.CLOSED})
    check("the window is wanted",
          C.screen_wanted(shooting, readings, last_ok, 1000.0), True)
    state, why = C.hall_state(shooting, "E2", readings, last_ok, 1000.0,
                              screen)
    check("E2 is being shot into", state, C.STATE_SHOOTING)
    check("and the sentence says where it was read",
          "L3BT MSS window" in why, True)
    check("E4 is not", C.hall_state(shooting, "E4", readings, last_ok,
                                    1000.0, screen)[0], C.STATE_IDLE)
    check("E2's limits are in force, no fallback",
          C.halls_in_force(shooting, readings, last_ok, 1000.0, screen),
          (["E2"], False))
    check("the one square is E2",
          C.single_hall(shooting, readings, last_ok, 1000.0, screen), "E2")

    print("all shut on the window: nothing is being shot into E2-E5")
    shut = FakeScreen({h: C.CLOSED for h in ("E2", "E3", "E4", "E5")})
    check("no hall, no fallback",
          C.halls_in_force(shooting, readings, last_ok, 1000.0, shut),
          ([], False))
    check("the one square is Int",
          C.single_hall(shooting, readings, last_ok, 1000.0, shut), "Int")

    print("an old look is not believed")
    old = FakeScreen({"E2": C.OPEN}, at=1000.0 - C.STALE_AFTER_S - 1)
    check("back to watching Int",
          C.halls_in_force(shooting, readings, last_ok, 1000.0, old),
          (["Int"], True))

    print("a window that could not be read says why")
    gone = FakeScreen({}, problem="The L3BT MSS window is not open.")
    state, why = C.hall_state(shooting, "E2", readings, last_ok, 1000.0, gone)
    check("E2 is not known", state, C.STATE_UNKNOWN)
    check("and the tooltip names the window", "not open" in why, True)

    print("the channel wins the day it publishes")
    e2_open = C.default_gate("E2")["open"]
    readings[e2_open] = FakeReading(value=0.0)
    last_ok[e2_open] = 1000.0
    state, why = C.hall_state(shooting, "E2", readings, last_ok, 1000.0,
                              screen)
    check("the channel says shut, so E2 is idle", state, C.STATE_IDLE)
    check("and the sentence names the channel", e2_open in why, True)
    for hall in ("E3", "E4", "E5"):
        pv = C.default_gate(hall)["open"]
        readings[pv] = FakeReading(value=0.0)
        last_ok[pv] = 1000.0
    check("all four publish: the window is not wanted any more",
          C.screen_wanted(shooting, readings, last_ok, 1000.0), False)


def test_what_a_one_means_comes_from_the_channel_name():
    print("1 is open on one channel and SHUT on the other")
    check("an open-position channel",
          C.switch_sense("L3-PSS:SGV502_IN_OPEN_POSITION"), C.OPEN)
    check("a closed-position channel",
          C.switch_sense("L3-PSS:SGV502_IN_CLOSED_POSITION"), C.CLOSED)
    check("a name that says neither claims nothing",
          C.switch_sense("L3-PSS:SGV502_POSITION"), None)
    check("nothing at all", C.switch_sense(""), None)

    print("so a closed-position channel is read the right way round")
    shooting = {"E2": {"open": "L3-PSS:SGV502_IN_CLOSED_POSITION"}}
    # The switch is MADE, and on that channel made means SHUT — so the beam is
    # not going into E2. Taken as an open switch it would have said it was.
    readings = {"L3-PSS:SGV502_IN_CLOSED_POSITION": FakeReading(value=1.0)}
    last_ok = {"L3-PSS:SGV502_IN_CLOSED_POSITION": 1000.0}
    state, why = C.hall_state(shooting, "E2", readings, last_ok, 1000.0)
    check("not shooting into E2", state, C.STATE_IDLE)
    check("and the sentence says the valve is shut", "closed" in why, True)
    readings["L3-PSS:SGV502_IN_CLOSED_POSITION"] = FakeReading(value=0.0)
    state, _why = C.hall_state(shooting, "E2", readings, last_ok, 1000.0)
    check("not made means open, which for E2 is shooting",
          state, C.STATE_SHOOTING)


def test_the_one_sentence_about_the_whole_program():
    print("what the Info / Log tab says about a machine nobody is shooting in")
    shooting = {h: C.default_gate(h) for h in C.HALLS}
    rows = [C.new_row(1, "TEST:PV", "Test value", "ns")]
    C.limits_for(rows[0], "Int").update({"min": 1.0, "max": 2.0})
    int_open = C.default_gate("Int")["open"]
    readings = {int_open: FakeReading(value=1.0),
                "TEST:PV": FakeReading(value=1.5)}
    last_ok = {int_open: 1000.0, "TEST:PV": 1000.0}
    line = C.status_line(rows, shooting, readings, last_ok, 1000.0)
    check("it names the valves that cannot be read",
          line.startswith("The E2, E3, E4, E5 valves cannot be read"), True)
    # Said with the name on screen, L3 — never "Int" (14.10.2026).
    check("L3's limits are in force", "L3's limits are in force" in line,
          True)
    check("one channel, in range", line.endswith("1 channel watched, "
                                                 "all in range"), True)

    print("and about one that is out of range")
    readings["TEST:PV"] = FakeReading(value=9.0)
    line = C.status_line(rows, shooting, readings, last_ok, 1000.0)
    check("it counts it", line.endswith("1 out of range"), True)

    print("and about one where the valves answer, but nobody is shooting")
    # E2 answers "shut", so the machine IS reporting and the Int fallback stays
    # out of the way. Nothing is being shot into, so nothing is judged.
    e2_open = C.default_gate("E2")["open"]
    for hall in C.HALLS[1:]:
        pv = C.default_gate(hall)["open"]
        readings[pv] = FakeReading(value=0.0)
        last_ok[pv] = 1000.0
    line = C.status_line(rows, shooting, readings, last_ok, 1000.0)
    check("nothing is judged",
          line, "No hall is being shot into — 1 channel listed, nothing judged")

    print("and about one hall really being shot into")
    # The limit was set for Int alone, so the same 9.0 is in range for E2 —
    # which is what "Min and Max belong to this hall" means.
    readings[e2_open] = FakeReading(value=1.0)
    line = C.status_line(rows, shooting, readings, last_ok, 1000.0)
    check("it says which hall",
          line, "Shooting into E2 — 1 channel watched, all in range")

    print("the reading clock")
    check("nothing yet", C.read_clock_text({}, 1000.0), "nothing read yet")
    check("just now", C.read_clock_text({"a": 999.5}, 1000.0), "read just now")
    check("a while ago", C.read_clock_text({"a": 988.0}, 1000.0),
          "last read 12 s ago")


def test_the_look_is_repaired_like_everything_else():
    print("a settings file with no look in it gets the defaults")
    cfg = {"rows": []}
    check("the whole block", C.look_of(cfg), dict(C.LOOK_DEFAULTS))
    check("and it was written back", cfg["look"], dict(C.LOOK_DEFAULTS))

    print("nonsense in the file never reaches the screen")
    cfg = {"look": {"family": "   ", "size_px": "big", "mark_px": None,
                    "bold": "yes"}}
    look = C.look_of(cfg)
    check("an empty typeface", look["family"], C.LOOK_DEFAULTS["family"])
    check("a size that is a word", look["size_px"],
          C.LOOK_DEFAULTS["size_px"])
    check("a square that is nothing", look["mark_px"],
          C.LOOK_DEFAULTS["mark_px"])
    check("bold is a yes or a no", look["bold"], True)

    print("and a size nobody could read is pulled back to the bounds")
    low, high = C.LOOK_LIMITS["size_px"]
    check("far too big", C.look_of({"look": {"size_px": 500}})["size_px"], high)
    check("far too small", C.look_of({"look": {"size_px": 1}})["size_px"], low)

    print("a sensible look survives the file untouched")
    kept = {"family": "Consolas", "size_px": 24, "bold": False,
            "mark_px": 36}
    back = C.normalise({"look": dict(kept)})["look"]
    check("all four", back, kept)


def test_the_automatic_arrangement_is_five_squares():
    print("the automatic arrangement is five squares of one size, centred")
    tiles = C.auto_hall_tiles(1160, 150)
    check("one per hall", sorted(tiles), sorted(C.HALLS))
    widths = {round(t[2] * 1160) for t in tiles.values()}
    heights = {round(t[3] * 150) for t in tiles.values()}
    check("all the same width", len(widths), 1)
    check("and really square", widths.pop(), heights.pop())
    xs = sorted(t[0] * 1160 for t in tiles.values())
    check("evenly spaced", {round(b - a) for a, b in zip(xs, xs[1:])},
          {round(150 + C.AUTO_GAP_PX)})
    check("and centred", round(xs[0]),
          round(1160 - (xs[-1] + 150)))

    print("a band too narrow for five squares shrinks them instead of leaving")
    tight = C.auto_hall_tiles(300, 150)
    check("the last one still ends inside the band",
          max(t[0] + t[2] for t in tight.values()) <= 1.0001, True)


def test_a_hand_made_arrangement_is_used_verbatim():
    print("a hand-made arrangement is used exactly as it was dragged")
    halls = C.halls_of({"halls": {
        "auto": False,
        "layout": {h: [0.1 * i, 0.0, 0.15, 0.6]
                   for i, h in enumerate(C.HALLS)}}})
    check("it stayed off automatic", halls["auto"], False)
    tiles = C.hall_tiles(halls, 1000, 200)
    check("Int is where it was put", list(tiles["Int"]), [0.0, 0.0, 0.15, 0.6])
    check("E5 too", [round(v, 3) for v in tiles["E5"]],
          [0.4, 0.0, 0.15, 0.6])

    print("and a rectangle outside the band is pulled back inside it")
    off = C.halls_of({"halls": {"auto": False, "layout": {
        h: [1.6, -0.4, 2.0, 0.3] for h in C.HALLS}}})
    tile = off["layout"]["E2"]
    check("width capped at the whole band", tile[2], 1.0)
    check("and then it can only sit at the left edge", tile[0], 0.0)
    check("nothing above the top either", tile[1], 0.0)

    print("a square dragged to nothing keeps a size it can be grabbed by")
    tiny = C.halls_of({"halls": {"auto": False, "layout": {
        h: [0.5, 0.5, 0.0, 0.0] for h in C.HALLS}}})
    check("width", tiny["layout"]["Int"][2], C.TILE_MIN_FRAC)
    check("height", tiny["layout"]["Int"][3], C.TILE_MIN_FRAC)


def test_an_arrangement_missing_a_hall_is_not_used():
    print("an arrangement that does not place every hall falls back")
    # Four rectangles and five halls: the fifth square would have nowhere to
    # go, so the whole arrangement is dropped rather than losing a square off
    # the first tab.
    halls = C.halls_of({"halls": {"auto": False, "layout": {
        h: [0.0, 0.0, 0.2, 1.0] for h in C.HALLS[:4]}}})
    check("back on automatic", halls["auto"], True)
    tiles = C.hall_tiles(halls, 1000, 150)
    check("and every hall still has a place", sorted(tiles), sorted(C.HALLS))

    print("a rectangle that is not a rectangle is simply not a rectangle")
    halls = C.halls_of({"halls": {"auto": False, "layout": {
        "Int": "somewhere", "E2": [0.1, 0.1], "E3": [0.1, 0.1, 0.2, 0.2],
        "E4": None, "E5": [0.1, "x", 0.2, 0.2]}}})
    check("only the one good entry was kept", sorted(halls["layout"]), ["E3"])
    check("so it is automatic", halls["auto"], True)


def test_the_band_is_repaired_like_everything_else():
    print("nonsense in the band's settings never reaches the screen")
    halls = C.halls_of({"halls": {"band_px": "tall", "word_px": None,
                                  "show_word": "yes"}})
    check("a height that is a word", halls["band_px"],
          C.HALLS_DEFAULTS["band_px"])
    check("a word size that is nothing", halls["word_px"],
          C.HALLS_DEFAULTS["word_px"])
    check("the switch is a yes or a no", halls["show_word"], True)

    print("and a band nobody could use is pulled back to the bounds")
    low, high = C.HALLS_LIMITS["band_px"]
    check("far too tall", C.halls_of({"halls": {"band_px": 4000}})["band_px"],
          high)
    check("far too short", C.halls_of({"halls": {"band_px": 1}})["band_px"],
          low)

    print("a settings file with no band in it gets the defaults")
    cfg = {}
    halls = C.halls_of(cfg)
    check("the whole block", {k: halls[k] for k in C.HALLS_DEFAULTS},
          dict(C.HALLS_DEFAULTS))
    check("and it was written back", cfg["halls"] is halls, True)

    print("a hand-made arrangement survives a save and a read")
    layout = {h: [0.05 * i, 0.1, 0.17, 0.8] for i, h in enumerate(C.HALLS)}
    back = C.normalise({"halls": {"auto": False, "band_px": 260,
                                  "show_word": False, "word_px": 20,
                                  "layout": layout}})["halls"]
    check("still by hand", back["auto"], False)
    check("the band", back["band_px"], 260)
    check("the word off", back["show_word"], False)
    check("its size", back["word_px"], 20)
    check("and every rectangle", back["layout"]["E3"], [0.1, 0.1, 0.17, 0.8])


def test_the_one_square_names_the_hall_in_force():
    print("the one square: the hall being shot into, Int when none")
    shooting = {h: C.default_gate(h) for h in C.HALLS}
    int_open = C.default_gate("Int")["open"]
    e3_open = C.default_gate("E3")["open"]
    readings = {int_open: FakeReading(value=1.0)}
    last_ok = {int_open: 1000.0}
    check("nothing else readable: Int",
          C.single_hall(shooting, readings, last_ok, 1000.0), "Int")
    readings[e3_open] = FakeReading(value=1.0)
    last_ok[e3_open] = 1000.0
    check("E3 shooting: E3",
          C.single_hall(shooting, readings, last_ok, 1000.0), "E3")
    readings[e3_open] = FakeReading(value=0.0)
    check("E3 answers idle and nobody shoots: Int",
          C.single_hall(shooting, readings, last_ok, 1000.0), "Int")

    print("the switch survives a save, and an old file has it off")
    check("off by default", C.halls_of({})["single"], False)
    check("kept", C.normalise({"halls": {"single": True}})["halls"]["single"],
          True)

    print("the one square's sizes fit beside the list")
    row_h = lambda t: t + 4                       # noqa: E731
    row_w = lambda t: 12 * t                      # noqa: E731
    size, mark, side, word = C.fit_single(1000, 600, 560, 20, 5, row_h, row_w)
    check("the square is no taller than the window", side <= 600, True)
    check("the rows fit the height", 5 * row_h(size) <= 560, True)
    check("and the width beside the square",
          row_w(size) <= 1000 - side - C.SINGLE_GAP_PX - 20, True)
    check("the verdict square is 1.5x the text",
          mark, round(size * C.FIT_MARK_RATIO))
    size, mark, side, word = C.fit_single(700, 600, 560, 20, 5, row_h, row_w)
    check("the square first: as big as the list at the least text allows",
          side, 700 - C.SINGLE_GAP_PX - 20 - row_w(C.FIT_TEXT_MIN_PX))
    check("the text never under its least size", size >= C.FIT_TEXT_MIN_PX,
          True)
    size, mark, side, word = C.fit_single(2000, 600, 560, 20, 5, row_h, row_w)
    check("a wide window: the square is the whole height", side, 600)
    check("and the text grows into the rest",
          size > C.FIT_TEXT_MIN_PX
          and row_w(size) <= 2000 - 600 - C.SINGLE_GAP_PX - 20, True)
    size, mark, side, word = C.fit_single(400, 600, 560, 20, 5, row_h,
                                          lambda t: 30 * t)
    check("a wide list makes the square give way",
          30 * size <= 400 - side - C.SINGLE_GAP_PX - 20, True)
    check("and the text drops under its least size only then",
          size < C.FIT_TEXT_MIN_PX and size >= 8, True)

    print("the five squares leave the list its least text size")
    size, mark, band, word = C.fit_sizes(1500, 1480, 500, 10, row_h, row_w)
    check("ten rows still at the least size",
          10 * row_h(size) <= 500 - band and size >= C.FIT_TEXT_MIN_PX, True)


def test_firing_needs_a_tenth_of_a_joule():
    print("SHOOTING needs SBW4 at 0.1 J or more; the newer name answers")
    hapls, l3 = C.FIRING_CHANNELS
    now = 1000.0

    def scene(**values):
        readings, last_ok = {}, {}
        for pv, (value, t_ns) in values.items():
            r = FakeReading(value=value)
            r.newest_ns = t_ns
            readings[pv], last_ok[pv] = r, now
        return readings, last_ok

    r, ok = scene(**{hapls: (0.021, 5), l3: (0.021, 5)})
    check("0.021 J is not firing", C.firing(r, ok, now)[0], False)
    r, ok = scene(**{hapls: (0.1, 5)})
    check("0.1 J is firing (inclusive)", C.firing(r, ok, now)[0], True)
    r, ok = scene(**{hapls: (0.9, 1), l3: (0.02, 9)})
    check("an old shot under the idle name loses to the newer sample",
          C.firing(r, ok, now)[0], False)
    check("nothing readable claims nothing", C.firing({}, {}, now)[0], None)


def test_a_shot_is_shooting_for_fifteen_seconds():
    print("SHOOTING lasts 15 s after the last new shot, then idle")
    hapls = C.FIRING_CHANNELS[0]
    now = 1000.0

    def scene(value, t_ns):
        r = FakeReading(value=value)
        r.newest_ns = t_ns
        return {hapls: r}, {hapls: now}

    w = C.FiringWatch()
    check("the shot already there at start is not counted",
          w.update(*scene(0.8, 1), now, 100.0)[0], False)
    check("still not, a while later", w.update(*scene(0.8, 1), now, 105.0)[0],
          False)
    check("a new shot is shooting", w.update(*scene(0.8, 2), now, 110.0)[0],
          True)
    check("14 s later still shooting",
          w.update(*scene(0.8, 2), now, 124.0)[0], True)
    check("15 s later idle", w.update(*scene(0.8, 2), now, 125.0)[0], False)
    check("the next shot switches it back",
          w.update(*scene(0.7, 3), now, 130.0)[0], True)
    check("a lone trigger under 0.1 J after it keeps it shooting",
          w.update(*scene(0.02, 4), now, 131.0)[0], True)
    check("until 15 s after the last real shot",
          w.update(*scene(0.02, 4), now, 145.0)[0], False)
    check("nothing readable claims nothing", w.update({}, {}, now, 146.0)[0],
          None)


def _stamped(recent, error=None):
    """A reading carrying the timestamps of its last samples, in seconds."""
    r = FakeReading(value=1.0, error=error)
    r.recent_ns = tuple(int(t * 1e9) for t in recent)
    r.newest_ns = r.recent_ns[-1] if r.recent_ns else None
    return r


def test_shots_are_counted_off_new_samples():
    print("the shot counter: the shown channels changing is a shot")
    sc = C.ShotCounter(reset_s=10)
    a, b = "PV:A", "PV:B"
    check("the samples already there at start are history",
          sc.update({a: _stamped([1, 2]), b: _stamped([1.05])}, [a, b], 100.0),
          0)
    check("one channel changing on its own is a shot",
          sc.update({a: _stamped([1, 2, 3]), b: _stamped([1.05])}, [a, b],
                    101.0), 1)
    check("the same shot on the other channel a pass later is not another",
          sc.update({a: _stamped([1, 2, 3]), b: _stamped([1.05, 3.05])},
                    [a, b], 102.0), 1)
    check("three shots inside one pass are three",
          sc.update({a: _stamped([3, 3.5, 4, 4.5]), b: _stamped([3.05])},
                    [a, b], 103.0), 4)

    print("three channels changing one after another are ONE shot")
    st = C.ShotCounter(reset_s=10)
    x, y, z = "PV:X", "PV:Y", "PV:Z"
    st.update({x: _stamped([1]), y: _stamped([1]), z: _stamped([1])},
              [x, y, z], 200.0)
    check("stamps 0.0 / 0.5 / 0.9 s apart, same pass",
          st.update({x: _stamped([1, 10.0]), y: _stamped([1, 10.5]),
                     z: _stamped([1, 10.9])}, [x, y, z], 201.0), 1)
    check("next shot: the channels arrive in two different passes",
          st.update({x: _stamped([10.0, 20.0]), y: _stamped([10.5, 20.6])},
                    [x, y, z], 202.0), 2)
    check("... and the third one late, a pass after",
          st.update({z: _stamped([10.9, 21.1])}, [x, y, z], 203.0), 2)
    check("an errored read counts nothing",
          sc.update({a: _stamped([9], error="HTTP 500")}, [a], 104.0), 4)
    check("a channel that is not watched counts nothing",
          sc.update({b: _stamped([3.05, 9])}, [a], 105.0), 4)

    print("quiet for the reset time: the number stays, the count starts again")
    sc.tick(112.9)
    check("9.9 s after the last shot it is still running", sc.running(), True)
    sc.tick(113.0)
    check("10 s after it the run is over", sc.running(), False)
    check("and the number is still on screen", sc.shown, 4)
    check("a late sample of the last run's shot opens nothing",
          sc.update({a: _stamped([4.5]), b: _stamped([3.05, 4.55])}, [a, b],
                    114.0), 4)
    check("the next shot counts from 1",
          sc.update({a: _stamped([4.5, 30])}, [a], 120.0), 1)

    print("which channels: the shown rows, and what a shown formula uses")
    rows = [C.new_row(1, "PV:A", "a"), C.new_row(2, "PV:B", "b"),
            C.new_row(3, "PV:C", "c"),
            C.new_formula_row(4, "f", "B * 2", bind={"B": 2}),
            C.new_shot_row(5)]
    rows[1]["show"] = False
    rows[2]["show"] = False
    check("shown plain row + the hidden row behind a shown formula",
          C.shot_channels(rows), ["PV:A", "PV:B"])

    print("the counter's row")
    values = C.row_values(rows, {}, {}, 1000.0, shots=7)
    check("its number is the count", values[5].value, 7.0)
    check("it is never stale", values[5].problem, None)
    check("on screen", C.value_text(values[5].value, ""), "7")
    back = C.normalise({"rows": rows + [C.new_shot_row(6)],
                        "shots": {"reset_s": 99999}})
    check("one counter on the list at most",
          sum(1 for r in back["rows"] if C.is_shot_row(r)), 1)
    check("the reset time is held to its limit", back["shots"]["reset_s"],
          C.SHOT_RESET_LIMITS[1])
    check("a new file has the default",
          C.shots_of(C.default_config())["reset_s"], C.SHOT_RESET_S_DEFAULT)
    old = C.normalise({"rows": [C.new_row(1, "PV:A", "a"),
                                C.new_row(2, "PV:B", "b")]})
    added = [r for r in old["rows"] if C.is_shot_row(r)]
    # DM settings is added straight after it (8.10.2026), unticked.
    check("a file without the counter gets it, shown, at the bottom",
          (len(added), added[0]["show"] if added else None,
           C.is_shot_row(old["rows"][-2]), added[0]["id"] if added else None),
          (1, True, True, 3))
    dm = [r for r in old["rows"] if C.is_dm_row(r)]
    check("and DM settings after it, not shown",
          (len(dm), C.is_dm_row(old["rows"][-1]),
           dm[0]["show"] if dm else None), (1, True, False))
    with tempfile.TemporaryDirectory() as tmp:
        fresh, _problem = C.load_config(Path(tmp) / "none.json")
    check("a first run has the counter",
          sum(1 for r in fresh["rows"] if C.is_shot_row(r)), 1)

    print("the counter's settings are one set for every hall")
    shot = C.new_shot_row(2, "Shots")
    shot["show"] = False
    seen = set()
    for hall in C.HALLS:
        cfg = C.normalise({"rows": [C.new_row(1, "PV:A", "a"), dict(shot)],
                           "shots": {"reset_s": 25, "size_px": 20, "bold": True},
                           "active_hall": hall})
        row = next(r for r in cfg["rows"] if C.is_shot_row(r))
        seen.add((tuple(sorted(C.shots_of(cfg).items())),
                  C.is_shown(row), row.get("name")))
    check("the same in every hall", len(seen), 1)


def test_l3_waits_for_a_hall():
    print("L3: SGV501 open and no hall's valve open is 'waiting for hall'")
    shooting = {h: C.default_gate(h) for h in C.HALLS}
    pv = {h: C.default_gate(h)["open"] for h in C.HALLS}
    now = 1000.0

    def scene(**open_):
        readings = {pv[h]: FakeReading(value=v) for h, v in open_.items()}
        return readings, {p: now for p in readings}

    check("Int's square is called L3", C.hall_shown("Int"), "L3")
    check("the others keep their names", C.hall_shown("E2"), "E2")
    _r, _ok = scene(Int=0.0)
    check("the valve's sentence says L3, not Int",
          "going into L3" in C.hall_state(shooting, "Int", _r, _ok, now)[1],
          True)
    check("SGV501 shut: not waiting (L3 shoots)",
          C.l3_waiting(shooting, *scene(Int=0.0), now)[0], False)
    check("SGV501 open, the others unreadable: waiting",
          C.l3_waiting(shooting, *scene(Int=1.0), now)[0], True)
    check("SGV501 open, every other valve shut: waiting",
          C.l3_waiting(shooting, *scene(Int=1.0, E2=0.0, E3=0.0, E4=0.0,
                                        E5=0.0), now)[0], True)
    check("SGV501 open into E3: not waiting, E3 lights",
          C.l3_waiting(shooting, *scene(Int=1.0, E3=1.0), now)[0], False)
    check("SGV501 unreadable: not waiting",
          C.l3_waiting(shooting, *scene(E2=0.0), now)[0], False)


def test_dead_valve_names_move_to_the_l3bt_archive():
    print("SGV502-505's old names, which never published, move to L3BT-MSS")
    back = C.normalise({"shooting": {
        "Int": {"open": "L3-PSS:SGV501_IN_OPEN_POSITION"},
        "E2": {"open": "L3-PSS:SGV502_IN_OPEN_POSITION"},
        "E3": "L3-PSS:SGV503_IN_OPEN_POSITION",
        "E4": {"open": "PV:TYPED"},
        "E5": {"open": ""}}})
    sh = back["shooting"]
    check("SGV501 stays where it publishes", sh["Int"]["open"],
          "L3-PSS:SGV501_IN_OPEN_POSITION")
    check("E2's dead default is moved", sh["E2"]["open"],
          "L3BT-MSS:SGV502_OPEN_IN")
    check("the old one-name shape is moved too", sh["E3"]["open"],
          "L3BT-MSS:SGV503_OPEN_IN")
    check("a name the operator typed is kept", sh["E4"]["open"], "PV:TYPED")
    check("a cleared name stays cleared", sh["E5"]["open"], "")
    print("a channel the archive marks as Disconnected is not a value")
    import ls_cpva
    raw = [{"time": 1, "severity": {"level": "OK", "hasValue": True},
            "value": [1]},
           {"time": 2, "severity": {"level": "INVALID", "hasValue": False},
            "value": ["Disconnected"]}]
    check("the Disconnected row is kept as no value",
          ls_cpva.parse_samples(raw), [(1, 1.0), (2, None)])


def main():
    for test in (test_limits_are_inclusive,
                 test_an_empty_box_is_not_zero,
                 test_nothing_read_is_never_a_tick,
                 test_no_hall_means_no_verdict,
                 test_each_hall_has_its_own_limits,
                 test_a_channel_added_is_added_in_every_hall,
                 test_a_stale_per_hall_show_is_stripped,
                 test_unticked_channel_cannot_blame_a_hall,
                 test_alarm_off_keeps_the_row_but_never_blinks,
                 test_scanning_judges_on_the_scan_limits,
                 test_the_little_square_grey_vs_orange,
                 test_a_formula_may_use_a_row_below_it,
                 test_a_stale_reading_cannot_raise_a_hall,
                 test_a_position_switch,
                 test_the_open_switch_answers_on_its_own,
                 test_int_shoots_while_its_valve_is_shut,
                 test_only_the_open_switch_is_read,
                 test_the_closed_switch_is_only_a_fallback,
                 test_a_valve_nobody_has_touched_is_still_read,
                 test_the_old_settings_files_still_open,
                 test_a_formula_is_worked_out_of_the_rows_above_it,
                 test_a_formula_with_nothing_under_it_is_a_dash,
                 test_a_formula_that_cannot_be_read_back,
                 test_a_formula_cannot_run_anything,
                 test_the_letters_follow_the_rows,
                 test_limits_survive_a_save,
                 test_a_broken_file_is_not_a_lost_file,
                 test_search_is_a_filter,
                 test_a_comma_is_a_decimal_point,
                 test_a_comma_in_a_formula,
                 test_a_new_formula_row_stays_a_formula,
                 test_a_formula_typed_into_the_channel_box_is_repaired,
                 test_int_is_watched_while_the_other_valves_are_silent,
                 test_the_mss_window_answers_while_the_valves_are_silent,
                 test_what_a_one_means_comes_from_the_channel_name,
                 test_the_one_sentence_about_the_whole_program,
                 test_the_look_is_repaired_like_everything_else,
                 test_the_automatic_arrangement_is_five_squares,
                 test_a_hand_made_arrangement_is_used_verbatim,
                 test_an_arrangement_missing_a_hall_is_not_used,
                 test_the_band_is_repaired_like_everything_else,
                 test_the_one_square_names_the_hall_in_force,
                 test_firing_needs_a_tenth_of_a_joule,
                 test_a_shot_is_shooting_for_fifteen_seconds,
                 test_shots_are_counted_off_new_samples,
                 test_l3_waits_for_a_hall,
                 test_dead_valve_names_move_to_the_l3bt_archive):
        test()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for line in FAILED:
            print("  " + line)
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
