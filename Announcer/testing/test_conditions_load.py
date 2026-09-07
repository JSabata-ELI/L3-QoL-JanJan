"""What comes out of presets.json: the two old shapes must survive untouched,
the two new ones must load, and nothing broken may take a whole condition down
with it.

Run:  python test_conditions_load.py
"""
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402
import a               # noqa: E402

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"FAIL  {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok    {name}")


def png_b64(size=(8, 6)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class Loader:
    """Only the parts of ScreenTracker that reading conditions touches."""
    _next_cond_uid = a.ScreenTracker._next_cond_uid
    # staticmethod on the real class: re-wrap it, or it binds to self here.
    _decode_reference = staticmethod(a.ScreenTracker._decode_reference)
    _load_conditions = a.ScreenTracker._load_conditions
    _save_conditions = a.ScreenTracker._save_conditions
    _cond_summary = a.ScreenTracker._cond_summary

    def __init__(self, conditions):
        self._cond_uid_seq = 0
        self._presets = {"conditions": conditions}
        self._conditions = []
        self.saved = None

    def _save_presets_file(self):
        self.saved = self._presets["conditions"]


REF = png_b64()

RAW = [
    # the two shapes that already exist in the wild
    {"kind": "screen", "name": "Old screen", "enabled": True, "monitor": 1,
     "region": [10, 20, 110, 80], "threshold": 3.5,
     "message": "look at HP", "reference": REF},
    {"kind": "pv", "name": "Old value", "enabled": False,
     "pv": "L3-PM03-023:Energy", "warn": 1.0, "trip": 2.0, "unit": "mJ",
     "message": "back reflection"},
    # a value condition with an area attached
    {"kind": "pv", "name": "Value plus area", "enabled": True,
     "pv": "L3-PM03-023:Energy", "warn": None, "trip": 2.0, "unit": "mJ",
     "message": "", "gate_monitor": 2, "gate_region": [1, 2, 51, 42],
     "gate_threshold": 4.0, "gate_reference": REF},
    # the same, but the area is corrupt: the value half must survive
    {"kind": "pv", "name": "Broken area", "enabled": True,
     "pv": "L3-PM03-023:Energy", "warn": None, "trip": 2.0, "unit": "",
     "message": "", "gate_region": [1, 2, "x", 42], "gate_reference": REF},
    # a hall condition
    {"kind": "hall", "name": "Into E4", "enabled": True, "message": "wrong hall",
     "hall": 3, "pss": 1},
    # and things that must be dropped
    {"kind": "nonsense", "name": "Dropped"},
    {"kind": "screen", "name": "No region", "region": [1, 2]},
    {"kind": "pv", "name": "No PV name", "pv": "  "},
]

L = Loader(RAW)
conds = L._load_conditions()
L._conditions = conds
by_name = {c["name"]: c for c in conds}

check("only the usable ones load", sorted(by_name),
      ["Broken area", "Into E4", "Old screen", "Old value", "Value plus area"])

s = by_name["Old screen"]
check("a screen condition keeps its region", s["region"], [10, 20, 110, 80])
check("its threshold is a number", s["threshold"], 3.5)
check("its monitor survives", s["monitor"], 1)
check("its reference picture decodes", s["_ref_img"].size, (8, 6))

v = by_name["Old value"]
check("an old value condition gains no area", "gate_region" in v, False)
check("its off state survives", v["enabled"], False)
check("its limits survive", (v["warn"], v["trip"]), (1.0, 2.0))

g = by_name["Value plus area"]
check("an attached area keeps its region", g["gate_region"], [1, 2, 51, 42])
check("its own sensitivity", g["gate_threshold"], 4.0)
check("its own monitor", g["gate_monitor"], 2)
check("its own picture decodes", g["_gate_img"].size, (8, 6))
check("the summary mentions the area", "+ screen area" in L._cond_summary(g), True)

bk = by_name["Broken area"]
check("a corrupt area is dropped, the condition is not",
      ("gate_region" in bk, bk["trip"]), (False, 2.0))
check("and its leftovers go with it",
      [k for k in bk if k.startswith("gate")], [])

h = by_name["Into E4"]
check("a hall condition keeps both halves", (h["hall"], h["pss"]), (3.0, 1.0))
check("and gets the default grace", h["moving_grace_s"], a.HALL_MOVING_GRACE_S)
check("its summary names the hall and the state",
      L._cond_summary(h), "beam to E4 · PSS into the experiment")

# A hall condition with neither half set says so instead of looking configured.
empty = Loader([{"kind": "hall", "name": "Empty", "enabled": True}])._load_conditions()[0]
check("an empty hall condition is None, not 0",
      (empty["hall"], empty["pss"]), (None, None))
check("and its summary admits it cannot fire",
      L._cond_summary(empty), "nothing set — it cannot fire")

# Saving must not write the decoded pictures or the uid back into the file.
L._save_conditions()
leaked = sorted({k for c in L.saved for k in c if k.startswith("_")})
check("nothing private is written to the file", leaked, [])
check("but the encoded pictures are",
      all("reference" in c or "gate_reference" in c or c["kind"] in ("pv", "hall")
          for c in L.saved), True)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    sys.exit(1)
print("all passed")
