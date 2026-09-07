"""
The reference judge: is a number inside the range somebody set for it, and is
it close enough to an edge to be worth a warning?

No GUI, no network, no files.

Run:  python testing/test_spfe_limits.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spfe_limits as limits            # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    if ok:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name}   {detail}")
        FAILED.append(name)


def level(value, entry, pct=5.0):
    return limits.judge(value, entry, default_warn_pct=pct).level


def test_no_reference_means_no_colour():
    check("a quantity nobody set a range for is never coloured",
          level(1234, None) == "" and level(1234, {}) == "")
    check("a reference with neither edge says nothing",
          level(1234, {"min": "", "max": ""}) == "")
    check("and has_range agrees",
          not limits.has_range({}) and limits.has_range({"min": 1}))


def test_inside_and_outside():
    band = {"min": 100, "max": 200}
    check("comfortably inside is not coloured", level(150, band) == "")
    check("below the low edge is red", level(99, band) == "bad")
    check("above the high edge is red", level(201, band) == "bad")
    check("exactly on an edge is still inside, but close to it",
          level(100, band) == "warn" and level(200, band) == "warn")


def test_close_to_an_edge():
    band = {"min": 100, "max": 200}      # 5 % of a range of 100 is 5
    check("within 5 % of the low edge is yellow", level(104, band) == "warn")
    check("within 5 % of the high edge is yellow", level(196, band) == "warn")
    check("six away from the edge is not", level(106, band) == "")
    check("a wider warning catches more",
          level(110, band, pct=15.0) == "warn")
    check("and a warning of nothing catches nothing",
          level(100.5, band, pct=0.0) == "")
    check("a reference can carry its own percentage",
          level(110, {"min": 100, "max": 200, "warn_pct": 15}) == "warn")


def test_one_edge_only():
    check("a minimum alone still catches a value under it",
          level(90, {"min": 100}) == "bad")
    check("and there is no ceiling to be near",
          level(100_000, {"min": 100}) == "")
    check("5 % of the edge itself is the band when there is only one",
          level(104, {"min": 100}) == "warn" and level(106, {"min": 100}) == "")
    check("a maximum alone works the same way",
          [level(210, {"max": 200}), level(196, {"max": 200}),
           level(150, {"max": 200})] == ["bad", "warn", ""])


def test_nothing_usable_is_never_an_alarm():
    band = {"min": 100, "max": 200}
    check("an empty cell is not judged", level("", band) == "")
    check("a dash is not judged", level("-", band) == "")
    check("a word is not judged", level("n/a", band) == "")
    check("a comma is read as a decimal point", level("150,5", band) == "")
    check("a range typed as text still works",
          level(99, {"min": "100", "max": "200"}) == "bad")


def test_a_reversed_range_is_put_right():
    entry = limits.normalise({"min": 200, "max": 100})
    check("a minimum above the maximum is swapped, not refused",
          [entry["min"], entry["max"]] == [100.0, 200.0])
    check("and it then judges the right way round",
          level(150, {"min": 200, "max": 100}) == "")


def test_the_reason_says_what_is_wrong():
    v = limits.judge(99, {"min": 100, "max": 200})
    check("the sentence names the value and the edge",
          "99" in v.reason and "100" in v.reason, v.reason)
    v = limits.judge(196, {"min": 100, "max": 200})
    check("a warning says which edge it is near",
          "high edge" in v.reason, v.reason)
    check("a reference describes itself in one line",
          limits.describe({"min": 100, "max": 200}).startswith(
              "reference 100 to 200"),
          limits.describe({"min": 100, "max": 200}))
    check("a half reference too",
          limits.describe({"min": 100}).startswith("reference at least 100"),
          limits.describe({"min": 100}))


def test_worse_of_two():
    check("red beats yellow beats nothing",
          [limits.worse("", "warn"), limits.worse("warn", "bad"),
           limits.worse("bad", "warn")] == ["warn", "bad", "bad"])


def main():
    print("spfe_limits")
    for fn in (test_no_reference_means_no_colour,
               test_inside_and_outside,
               test_close_to_an_edge,
               test_one_edge_only,
               test_nothing_usable_is_never_an_alarm,
               test_a_reversed_range_is_put_right,
               test_the_reason_says_what_is_wrong,
               test_worse_of_two):
        fn()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed: {', '.join(FAILED)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
