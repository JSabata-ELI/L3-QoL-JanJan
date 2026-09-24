"""What ann_core promises, checked without a window and without a network.

Run:  python testing/test_core.py
      or  py -m pytest testing/test_core.py

Nothing here touches the real presets.json. The migration test copies it into a
temporary folder first, so running the tests can never cost the operator a
rectangle.
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ann_core as C          # noqa: E402


# ── limits: empty is not zero ────────────────────────────────────────────────

def test_parse_level_empty_is_not_zero():
    assert C.parse_level("") is None
    assert C.parse_level("   ") is None
    assert C.parse_level(None) is None
    assert C.parse_level("0") == 0.0          # a typed zero IS a limit
    assert C.parse_level("1,5") == 1.5        # Czech keyboard
    assert C.parse_level("nonsense") is None
    assert C.level_text(None) == ""
    assert C.level_text(2.0) == "2"


# ── the value verdict, over every combination ───────────────────────────────

def _v(**kw):
    return C.new_value_item(1, "x", "PV:X", **kw)


def test_value_all_limits_off_never_fires():
    it = _v()
    assert C.judge_value(it, 1e9)[0] == C.STATE_OK
    assert C.judge_value(it, -1e9)[0] == C.STATE_OK


def test_value_trip_outranks_warn():
    it = _v(lo_lo=1, lo=2, hi=8, hi_hi=9)
    assert C.judge_value(it, 5)[0] == C.STATE_OK
    assert C.judge_value(it, 8.5)[0] == C.STATE_WARN
    assert C.judge_value(it, 9.5)[0] == C.STATE_TRIP
    assert C.judge_value(it, 1.5)[0] == C.STATE_WARN
    assert C.judge_value(it, 0.5)[0] == C.STATE_TRIP
    # Exactly on a limit is not past it.
    assert C.judge_value(it, 9)[0] == C.STATE_OK or C.judge_value(it, 9)[0] == C.STATE_WARN
    assert C.judge_value(it, 8)[0] == C.STATE_OK


def test_value_one_sided_limits():
    assert C.judge_value(_v(hi_hi=2), 3)[0] == C.STATE_TRIP
    assert C.judge_value(_v(hi_hi=2), -100)[0] == C.STATE_OK
    assert C.judge_value(_v(lo_lo=7), 3)[0] == C.STATE_TRIP
    assert C.judge_value(_v(lo_lo=7), 100)[0] == C.STATE_OK


def test_value_unreadable_is_never_ok():
    it = _v(hi_hi=2)
    state, text = C.judge_value(it, None, error="PV:X — Archiver unreachable")
    assert state == C.STATE_UNKNOWN
    assert "unreachable" in text
    state, text = C.judge_value(it, None, samples=0)
    assert state == C.STATE_UNKNOWN
    assert "nothing archived" in text


def test_value_stale_announces_itself():
    it = _v(hi_hi=99)
    state, text = C.judge_value(it, 1.0, age_s=C.STALE_AFTER_S + 5)
    assert state == C.STATE_STALE
    assert "not refreshed" in text
    # Fresh again, same value: a value that does not change is a healthy
    # machine, not a dead channel.
    assert C.judge_value(it, 1.0, age_s=1.0)[0] == C.STATE_OK


def test_value_switched_off():
    assert C.judge_value(_v(on=False, hi_hi=1), 99)[0] == C.STATE_OFF


def test_value_sentence_carries_the_unit():
    it = _v(unit="mJ", hi_hi=2)
    assert C.judge_value(it, 3.5)[1] == "3.5 mJ is over 2 mJ"
    assert C.judge_value(_v(hi_hi=2), 3.5)[1] == "3.5 is over 2"


# ── the area verdict ────────────────────────────────────────────────────────

def _a(**kw):
    kw.setdefault("region", [0, 0, 10, 10])
    kw.setdefault("reference", "x")
    return C.new_area_item(2, "area", **kw)


def test_area_needs_a_rectangle_and_a_reference():
    assert C.judge_area(C.new_area_item(2, "a"), 0.0)[0] == C.STATE_UNKNOWN
    state, text = C.judge_area(C.new_area_item(2, "a", region=[0, 0, 5, 5]), 0.0)
    assert state == C.STATE_UNKNOWN
    assert "no reference" in text


def test_area_threshold():
    it = _a(threshold=2.0)
    assert C.judge_area(it, 1.9)[0] == C.STATE_OK
    assert C.judge_area(it, 2.0)[0] == C.STATE_OK       # strictly over
    assert C.judge_area(it, 2.1)[0] == C.STATE_TRIP


def test_area_size_mismatch_is_a_trip_not_a_shrug():
    state, text = C.judge_area(_a(), 0.0, size_now=(100, 50), size_ref=(369, 183))
    assert state == C.STATE_TRIP
    assert "100x50" in text and "369x183" in text


def test_area_failed_grab_is_unknown():
    assert C.judge_area(_a(), None)[0] == C.STATE_UNKNOWN
    assert C.judge_area(_a(), 0.0, error="Screenshot failed")[0] == C.STATE_UNKNOWN


# ── firing: each item on its own ────────────────────────────────────────────

def test_each_item_fires_alone():
    a = C.new_value_item(1, "a", "PV:A", hi_hi=1)
    b = C.new_value_item(2, "b", "PV:B", hi_hi=1)
    states = {1: C.STATE_TRIP, 2: C.STATE_OK}
    assert C.firing_ids([a, b], states) == {1}


def test_being_in_a_preset_does_not_hold_an_item_back():
    """The old group waited for its other half. A preset never does."""
    a = C.new_value_item(1, "a", "PV:A", hi_hi=1, presets=["pair"])
    b = C.new_value_item(2, "b", "PV:B", hi_hi=1, presets=["pair"])
    assert C.firing_ids([a, b], {1: C.STATE_TRIP, 2: C.STATE_OK}) == {1}
    assert C.firing_ids([a, b], {1: C.STATE_TRIP, 2: C.STATE_TRIP}) == {1, 2}
    # A warning is still not a trip.
    assert C.firing_ids([a, b], {1: C.STATE_WARN, 2: C.STATE_OK}) == set()


def test_a_switched_off_item_never_fires():
    a = C.new_value_item(1, "a", "PV:A", hi_hi=1)
    b = C.new_value_item(2, "b", "PV:B", hi_hi=1, on=False)
    assert C.firing_ids([a, b], {1: C.STATE_TRIP, 2: C.STATE_TRIP}) == {1}


# ── presets: the sets of alarms ─────────────────────────────────────────────

def _four():
    return [C.new_value_item(1, "a", "PV:A", presets=["night"]),
            C.new_value_item(2, "b", "PV:B", presets=["night", "day"]),
            C.new_value_item(3, "c", "PV:C", presets=["day"]),
            C.new_value_item(4, "d", "PV:D")]


def test_a_preset_shows_only_its_own():
    items = _four()
    assert [it["id"] for it in C.items_in_preset(items, "night")] == [1, 2]
    assert [it["id"] for it in C.items_in_preset(items, "day")] == [2, 3]


def test_all_shows_everything_and_unassigned_shows_the_loose_ones():
    items = _four()
    assert len(C.items_in_preset(items, C.PRESET_ALL)) == 4
    assert [it["id"] for it in
            C.items_in_preset(items, C.PRESET_UNASSIGNED)] == [4]


def test_a_preset_name_is_tidied_and_never_repeated():
    it = C.new_value_item(1, "a", "PV:A", presets=["  night ", "night", ""])
    assert it["presets"] == ["night"]


def test_renaming_a_preset_carries_its_alarms():
    cfg = {}
    items = _four()
    C.add_alarm_preset(cfg, "night")
    assert C.rename_alarm_preset(cfg, items, "night", "late shift") is None
    assert C.alarm_presets(cfg) == ["late shift"]
    assert [it["id"] for it in C.items_in_preset(items, "late shift")] == [1, 2]


def test_deleting_a_preset_keeps_its_alarms_as_unassigned():
    cfg = {"alarm_presets": ["night", "day"]}
    items = _four()
    C.delete_alarm_preset(cfg, items, "night")
    assert C.alarm_presets(cfg) == ["day"]
    # Item 1 was only in "night", so it is loose now; item 2 is still in "day".
    assert [it["id"] for it in
            C.items_in_preset(items, C.PRESET_UNASSIGNED)] == [1, 4]
    assert [it["id"] for it in C.items_in_preset(items, "day")] == [2, 3]


def test_two_presets_cannot_share_a_name():
    cfg = {}
    assert C.add_alarm_preset(cfg, "night") is None
    assert C.add_alarm_preset(cfg, "night") is not None
    assert C.add_alarm_preset(cfg, "   ") is not None
    assert C.alarm_presets(cfg) == ["night"]


def test_the_counts_include_the_loose_ones():
    cfg = {"alarm_presets": ["night", "day"]}
    counts, loose = C.preset_counts(_four(), cfg)
    assert counts == {"night": 2, "day": 2}
    assert loose == 1


def test_an_old_group_becomes_a_preset():
    """The name the operator typed is kept; the AND rule is not."""
    cfg = {"items": [
        dict(C.new_value_item(1, "a", "PV:A", hi_hi=1), group="pair"),
        dict(C.new_value_item(2, "b", "PV:B", hi_hi=1), group="pair"),
    ]}
    for it in cfg["items"]:
        it.pop("presets", None)
    items, notes = C.migrate(cfg)
    assert all("group" not in it for it in items)
    assert [it["presets"] for it in items] == [["pair"], ["pair"]]
    assert C.alarm_presets(cfg) == ["pair"]
    assert any("pair" in n for n in notes)
    # And they no longer wait for each other.
    assert C.firing_ids(items, {1: C.STATE_TRIP, 2: C.STATE_OK}) == {1}


def test_a_preset_named_only_on_an_item_still_reaches_the_dropdown():
    cfg = {"items": [dict(C.new_value_item(1, "a", "PV:A"),
                          presets=["typed by hand"])]}
    items, _notes = C.migrate(cfg)
    assert C.alarm_presets(cfg) == ["typed by hand"]
    assert len(C.items_in_preset(items, "typed by hand")) == 1
    assert C.items_in_preset(items, C.PRESET_UNASSIGNED) == []


# ── ids ─────────────────────────────────────────────────────────────────────

def test_ids_are_never_reused():
    items = C.default_value_items()
    n = C.next_id(items)
    assert n == max(i["id"] for i in items) + 1
    del items[0]
    assert C.next_id(items) == n      # deleting a row does not free its id


# ── the standard values ─────────────────────────────────────────────────────

def test_fourteen_standard_values():
    items = C.default_value_items()
    assert len(items) == 14
    assert all(i["fires"] == "show" for i in items), \
        "the pre-filled values only colour themselves, as they always have"
    assert all(i["read"] == "mean" for i in items), \
        "a single chiller sample crosses its limit constantly; the average does not"
    # Every chiller is two rows: one for holding the setpoint, one for the
    # setpoint being right at all.
    dev = [i for i in items if i.get("minus")]
    assert len(dev) == 6
    for d in dev:
        assert d["minus"].endswith(":TempSP") and d["pv"].endswith(":Temp")
    util = [i for i in items if i["name"].startswith("Utility chiller")]
    abs_row = [i for i in util if not i.get("minus")][0]
    assert (abs_row["lo_lo"], abs_row["hi_hi"]) == (18.0, 22.0), \
        "the Utility chiller runs warm on purpose"


def test_channel_search_is_a_filter_not_an_error():
    names = ["L3-UTIL-CHL03-001:Temp", "L3-UTIL-CHL03-001:TempSP",
             "L3-UTIL-CHL03-002:Temp", "L3-PM03-023:Energy",
             "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"]
    got, total = C.search_channels(names, "chl temp")
    assert total == 3 and len(got) == 3
    assert got[0] == "L3-UTIL-CHL03-001:Temp", \
        "the shortest in-order match comes first"
    # Words may be anywhere and need not touch.
    assert C.search_channels(names, "energy")[1] == 2
    # A word that fits nothing gives nothing — not everything.
    assert C.search_channels(names, "chl banana") == ([], 0)
    # Nothing typed is not a search for everything.
    assert C.search_channels(names, "") == ([], 0)
    assert C.search_channels(names, "   ") == ([], 0)


def test_channel_search_honours_the_order_typed():
    names = ["A:TempSetpoint", "A:SetpointTemp"]
    got, _ = C.search_channels(names, "set temp")
    assert got[0] == "A:SetpointTemp"
    got, _ = C.search_channels(names, "temp set")
    assert got[0] == "A:TempSetpoint"


def test_channel_search_says_how_many_it_is_showing():
    names = [f"PV:{i:04d}" for i in range(1000)]
    got, total = C.search_channels(names, "pv", limit=10)
    assert len(got) == 10 and total == 1000, \
        "the caller has to be able to say 'showing 10 of 1000'"


def test_channel_label_says_what_it_measures():
    items = C.default_value_items()
    dev = [i for i in items if i.get("minus")][0]
    assert " − " in C.channel_label(dev)


# ── the config file ─────────────────────────────────────────────────────────

def test_reserved_keys_are_not_presets():
    cfg = {"TEST": [1, 2, 3, 4], "flash_mode": "image", "conditions": [],
           "window_geometry": "1x1+0+0"}
    assert C.preset_names(cfg) == ["TEST"]
    assert C.preset_region(cfg, "TEST") == [1, 2, 3, 4]


def test_a_preset_cannot_be_named_after_a_setting():
    cfg = {"conditions": [{"kind": "pv", "pv": "PV:A"}]}
    err = C.set_preset_region(cfg, "conditions", [0, 0, 1, 1])
    assert err and "region name" in err
    assert cfg["conditions"] == [{"kind": "pv", "pv": "PV:A"}], \
        "the condition list must still be there"


def test_a_preset_keeps_its_window_geometry():
    cfg = {"P": {"region": [0, 0, 1, 1], "window_geometry": "10x10+5+5"}}
    assert C.set_preset_region(cfg, "P", [9, 9, 20, 20]) is None
    assert cfg["P"]["region"] == [9, 9, 20, 20]
    assert cfg["P"]["window_geometry"] == "10x10+5+5"


def test_save_is_atomic_and_round_trips():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "presets.json"
        cfg = {"TEST": [1, 2, 3, 4], "items": C.default_value_items()}
        assert C.save_config(cfg, p) is None
        assert not list(Path(d).glob("*.tmp")), "no temporary file left behind"
        back, problem = C.load_config(p)
        assert problem is None
        assert back == cfg


def test_a_broken_file_says_so():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "presets.json"
        p.write_text("{not json", encoding="utf-8")
        cfg, problem = C.load_config(p)
        assert cfg == {} and problem and "could not be read" in problem
        p.write_text("[1,2,3]", encoding="utf-8")
        cfg, problem = C.load_config(p)
        assert cfg == {} and problem and "does not hold settings" in problem


# ── migration ───────────────────────────────────────────────────────────────

def test_migrate_the_real_settings_file():
    """The operator's own presets.json comes out the other side intact."""
    real = C.program_dir() / C.CONFIG_NAME
    if not real.exists():
        return
    with tempfile.TemporaryDirectory() as d:
        copy = Path(d) / "presets.json"
        copy.write_bytes(real.read_bytes())
        cfg, problem = C.load_config(copy)
        assert problem is None
        items, notes = C.migrate(cfg)
        # Every saved rectangle is still a preset, under its own name.
        for name in C.preset_names(cfg):
            assert C.preset_region(cfg, name) is not None, \
                f'preset "{name}" lost its rectangle'
        # The fourteen standard values are there, and the operator's own limits
        # from the old PV Limits table came with them.
        assert len([i for i in items if i["kind"] == "value"]) >= 14
        helium = [i for i in items if i["name"] == "Helium volume"][0]
        saved = (cfg.get("pv_thresholds") or {}).get("L3-UTIL-HEB03-001:PressOut_PSI")
        if isinstance(saved, dict) and "red" in saved:
            assert helium["lo_lo"] == float(saved["red"]), \
                "the limit the operator typed must win over the table default"
        cfg["items"] = items
        assert C.save_config(cfg, copy) is None


def test_migrate_a_screen_condition_keeps_its_picture():
    cfg = {"conditions": [{
        "kind": "screen", "name": "L3BT alignment", "enabled": True,
        "monitor": 1, "region": [2919, 166, 3288, 349], "threshold": 2.5,
        "message": "look at the HP panel", "reference": "BASE64HERE"}]}
    items, _ = C.migrate(cfg)
    area = [i for i in items if i["kind"] == "area"][0]
    assert area["name"] == "L3BT alignment"
    assert area["region"] == [2919, 166, 3288, 349]
    assert area["threshold"] == 2.5
    assert area["reference"] == "BASE64HERE"
    assert area["fires"] == "alarm"
    assert area["message"] == "look at the HP panel"


def test_migrate_a_value_condition_maps_warn_and_trip():
    cfg = {"conditions": [{
        "kind": "pv", "name": "Back reflection", "enabled": True,
        "pv": "L3-PM03-023:Energy", "warn": 1.0, "trip": 2.0, "unit": "mJ",
        "message": "over the limit"}]}
    items, _ = C.migrate(cfg)
    val = [i for i in items if i["name"] == "Back reflection"][0]
    assert (val["hi"], val["hi_hi"]) == (1.0, 2.0)
    assert val["lo"] is None and val["lo_lo"] is None, "empty stays empty, not 0"
    assert val["read"] == "peak", "one shot over the limit is the whole event"
    assert val["fires"] == "alarm"


def test_migrate_an_attached_area_becomes_its_own_row():
    cfg = {"conditions": [{
        "kind": "pv", "name": "Back reflection", "enabled": True,
        "pv": "L3-PM03-023:Energy", "trip": 2.0,
        "message": "over the limit",
        "gate_monitor": 1, "gate_region": [10, 20, 110, 220],
        "gate_threshold": 3.0, "gate_reference": "PIC"}]}
    items, notes = C.migrate(cfg)
    names = [i["name"] for i in items]
    assert "Back reflection" in names
    assert "Back reflection · area" in names
    area = [i for i in items if i["name"] == "Back reflection · area"][0]
    assert area["region"] == [10, 20, 110, 220]
    assert area["threshold"] == 3.0 and area["reference"] == "PIC"
    assert any("row of its own" in n for n in notes), "the operator must be told"


def test_migrate_drops_a_hall_condition_out_loud():
    cfg = {"conditions": [{"kind": "hall", "name": "Shooting into E4",
                           "enabled": True, "hall": 3, "pss": 1}]}
    items, notes = C.migrate(cfg)
    assert not [i for i in items if i["name"] == "Shooting into E4"]
    assert any("Shooting into E4" in n and "dropped" in n for n in notes)


def test_migrate_drops_wreckage_without_taking_the_rest():
    cfg = {"conditions": [
        {"kind": "screen", "name": "broken", "region": [1, 2]},
        {"kind": "pv", "name": "nameless channel", "pv": "   "},
        {"kind": "pv", "name": "good", "pv": "PV:OK", "trip": 1.0},
    ]}
    items, notes = C.migrate(cfg)
    names = [i["name"] for i in items]
    assert "good" in names
    assert "broken" not in names and "nameless channel" not in names
    assert len([n for n in notes if "dropped" in n]) == 2


def test_migrate_is_idempotent():
    cfg = {"conditions": [{"kind": "pv", "name": "a", "pv": "PV:A", "trip": 1}]}
    items, _ = C.migrate(cfg)
    cfg["items"] = items
    again, notes = C.migrate(cfg)
    assert [i["id"] for i in again] == [i["id"] for i in items]
    assert [i["name"] for i in again] == [i["name"] for i in items]
    assert notes == []


# ── readable failures ───────────────────────────────────────────────────────

def test_readable_pv_error_says_what_to_do():
    import urllib.error
    msg, hint = C.readable_pv_error(
        "PV:X", urllib.error.HTTPError("u", 500, "boom", {}, None))
    assert "HTTP 500" in msg and "temporary" in hint
    msg, hint = C.readable_pv_error("PV:X", TimeoutError())
    assert "timed out" in msg and hint
    msg, hint = C.readable_pv_error("PV:X", RuntimeError("odd"))
    assert "odd" in msg and hint


# ── reference pictures ──────────────────────────────────────────────────────

def test_reference_round_trip_and_the_size_cap():
    from PIL import Image
    img = Image.new("RGB", (40, 30), (12, 34, 56))
    text, err = C.encode_reference(img)
    assert err is None and text
    back = C.decode_reference(text)
    assert back is not None and back.size == (40, 30)
    assert C.decode_reference(None) is None
    assert C.decode_reference("not base64") is None
    # A whole screen is refused with a sentence, not written as a megabyte.
    import random
    big = Image.new("RGB", (2000, 2000))
    big.putdata([(random.randrange(256), random.randrange(256),
                  random.randrange(256)) for _ in range(2000 * 2000)])
    text, err = C.encode_reference(big)
    assert text is None and err and "smaller area" in err


# ── taking rows off the list ────────────────────────────────────────────────

def test_drop_items_removes_exactly_what_was_asked_for():
    items = [C.new_value_item(1, "a"), C.new_value_item(2, "b"),
             C.new_value_item(3, "c")]
    gone = C.drop_items(items, [items[0], items[2]])
    assert gone == 2
    assert [it["name"] for it in items] == ["b"]


def test_drop_items_keeps_the_same_list_object():
    """The window owns the list and the engine and three tables watch it."""
    items = [C.new_value_item(1, "a"), C.new_value_item(2, "b")]
    same = items
    C.drop_items(items, [items[0]])
    assert same is items
    assert len(items) == 1


def test_drop_items_takes_the_row_asked_for_and_not_its_twin():
    """THE trap. Two identical rows are EQUAL dicts, so `list.remove` takes the
    first one — it would delete a different row than the one selected, and the
    selected one would still be sitting there afterwards."""
    a = C.new_value_item(1, "chiller")
    b = C.new_value_item(2, "chiller")
    b.update({k: v for k, v in a.items() if k != "id"})
    assert {k: v for k, v in a.items() if k != "id"} == \
           {k: v for k, v in b.items() if k != "id"}, "not twins, test is void"
    items = [a, b]
    C.drop_items(items, [b])
    assert len(items) == 1
    assert int(items[0]["id"]) == 1, "it took the wrong row"


def test_drop_items_with_nothing_selected_changes_nothing():
    items = [C.new_value_item(1, "a")]
    assert C.drop_items(items, []) == 0
    assert len(items) == 1


def test_drop_items_ignores_a_row_that_is_already_gone():
    items = [C.new_value_item(1, "a")]
    ghost = C.new_value_item(99, "gone")
    assert C.drop_items(items, [ghost]) == 0
    assert len(items) == 1


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            bad += 1
            print(f"  ERROR {name}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
