"""Each preset has its own place for the circle and the alarm picture.

Run:  python testing/test_placements.py

Asked for on 2026-09-18: the operations preset and the night one do not want
the circle in the same corner, and there has to be one press that gives the
places to every preset that has none yet.

Three things have to hold and each has its own test:

  * a place set under one preset must not move the other presets;
  * a preset that has never been placed follows the last place used anywhere,
    so nothing jumps the first time this build is started — but one that was
    deliberately put back to the corner stays in the corner;
  * "use these places for every preset that has none" skips the presets that
    have been placed by hand.

No Qt, no window: this is the settings file and nothing else.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import ann_core as C                                           # noqa: E402


def _cfg():
    return {"alarm_presets": ["Operations", "Night"]}


def test_two_presets_keep_two_different_places():
    cfg = _cfg()
    C.set_placement(cfg, "Operations", "hud", [100, 200])
    C.set_placement(cfg, "Night", "hud", [900, 40])
    assert C.placement(cfg, "Operations", "hud") == [100, 200]
    assert C.placement(cfg, "Night", "hud") == [900, 40]


def test_the_circle_and_the_picture_are_separate():
    cfg = _cfg()
    C.set_placement(cfg, "Operations", "hud", [10, 20])
    C.set_placement(cfg, "Operations", "alarm", [30, 40, 300, 200])
    assert C.placement(cfg, "Operations", "hud") == [10, 20]
    assert C.placement(cfg, "Operations", "alarm") == [30, 40, 300, 200]


def test_a_preset_never_placed_follows_the_old_single_setting():
    """What every existing settings file looks like on the first start."""
    cfg = _cfg()
    cfg["hud_position"] = [777, 55]
    cfg["alarm_geometry"] = [10, 10, 400, 300]
    assert C.placement(cfg, "Night", "hud") == [777, 55]
    assert C.placement(cfg, "Night", "alarm") == [10, 10, 400, 300]
    assert not C.has_placement(cfg, "Night", "hud")


def test_put_back_in_the_corner_is_not_the_same_as_never_placed():
    cfg = _cfg()
    cfg["hud_position"] = [777, 55]
    C.clear_placement(cfg, "Night", "hud")
    assert C.placement(cfg, "Night", "hud") is None, \
        "the reset preset went back to borrowing the old place"
    assert C.has_placement(cfg, "Night", "hud"), \
        "the reset was not remembered as a choice"
    # and the other presets are untouched by that reset
    assert C.placement(cfg, "Operations", "hud") == [777, 55]


def test_spread_fills_only_the_presets_with_no_place_of_their_own():
    cfg = _cfg()
    C.set_placement(cfg, "Operations", "hud", [100, 200])
    C.set_placement(cfg, "Operations", "alarm", [1, 2, 300, 400])
    C.set_placement(cfg, "Night", "hud", [900, 40])       # placed by hand
    filled = C.spread_placements(cfg, "Operations")
    assert "Night" in filled                  # it still had no alarm window
    assert C.placement(cfg, "Night", "hud") == [900, 40], \
        "a place set by hand was written over"
    assert C.placement(cfg, "Night", "alarm") == [1, 2, 300, 400]
    # All and Unassigned are presets in the drop-down, so they are filled too.
    assert C.placement(cfg, C.PRESET_ALL, "hud") == [100, 200]
    assert C.placement(cfg, C.PRESET_UNASSIGNED, "hud") == [100, 200]


def test_spread_a_second_time_changes_nothing():
    cfg = _cfg()
    C.set_placement(cfg, "Operations", "hud", [100, 200])
    C.spread_placements(cfg, "Operations")
    C.set_placement(cfg, "Night", "hud", [5, 5])
    assert C.spread_placements(cfg, "Operations") == [], \
        "presets that already have a place were offered again"
    assert C.placement(cfg, "Night", "hud") == [5, 5]


def test_renaming_a_preset_carries_its_places():
    cfg = _cfg()
    items = []
    C.set_placement(cfg, "Night", "hud", [900, 40])
    C.rename_alarm_preset(cfg, items, "Night", "Evening")
    assert C.placement(cfg, "Evening", "hud") == [900, 40]
    assert not C.has_placement(cfg, "Night", "hud")


def test_deleting_a_preset_forgets_its_places():
    cfg = _cfg()
    C.set_placement(cfg, "Night", "hud", [900, 40])
    C.delete_alarm_preset(cfg, [], "Night")
    assert not C.has_placement(cfg, "Night", "hud")


def test_the_places_are_not_a_saved_rectangle():
    """The trap `RESERVED_KEYS` exists for: a setting in the Areas drop-down."""
    cfg = _cfg()
    C.set_placement(cfg, "Operations", "hud", [100, 200])
    assert C.PLACEMENTS_KEY not in C.preset_names(cfg)


def test_rubbish_in_the_file_is_ignored():
    cfg = _cfg()
    cfg[C.PLACEMENTS_KEY] = {"Night": {"hud": ["left", "top"]}}
    assert C.placement(cfg, "Night", "hud") is None
    cfg[C.PLACEMENTS_KEY] = "not a table at all"
    assert C.placement(cfg, "Night", "hud") is None
    C.set_placement(cfg, "Night", "hud", [1, 2])
    assert C.placement(cfg, "Night", "hud") == [1, 2]


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
        except Exception as exc:                               # noqa: BLE001
            bad += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
