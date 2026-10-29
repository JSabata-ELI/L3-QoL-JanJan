"""The window-free rules of Screenshots (ssq_core).

Pinned:
  * the output names: monitor shots, camera files, labels and their counter;
  * the planned output count (one output = a FILE name, more = a FOLDER name);
  * the preset file: round trip in order, v1 files migrate, a broken file
    falls back to the built-ins, and it lives beside the program;
  * the station rules: window mode greys cameras not shown here, L3BT is
    archive-only, the archive mode offers everything.

The tone curve, the palettes, the frame pick and the auto time grid have their
own tests (test_tone, test_palettes_match_image_tools, test_frame_pick,
test_auto_grid). When the Qt program replaced the Tk one (16.10.2026) it was
checked pixel for pixel and frame for frame against s.py; that comparison is
kept with the Tk code in C:\\Dev\\backup\\Screenshots_Tk_20261016.

Run:  python testing/test_core.py
"""
import tempfile
from pathlib import Path

import qt_harness as H   # noqa: F401  (sys.path)
import ssq_core as core

check = H.check

print("output names")
out = Path("D:/x")
t = core.monitor_targets(out, "2026-10-16__10-00-00", None, True, "", 3)
check("all screens name", t == [(out / "2026-10-16__10-00-00_all.png", None)])
t = core.monitor_targets(out, "RUN", [0, 2], False, "", 4)
check("per-monitor names", t == [(out / "RUN_monitor1.png", 0), (out / "RUN_monitor3.png", 2)])
t = core.monitor_targets(out, "RUN", None, True, "mine", 1)
check("single output takes the user's name", t == [(out / "mine.png", None)])
check("no monitors, no shots", core.monitor_targets(out, "RUN", None, False, "", 2) == [])
src = Path("x/PFM1_1700000000000000000.png")
ts = core.prague_file_ts(src)
check("Prague time of the frame's own stamp", ts == "2023-11-14__23-13-20", ts)
check("camera name", core.camera_file_name("PFM1_NF", src, "", 2, None) == f"PFM1_NF__{ts}.png")
check("camera single output", core.camera_file_name("PFM1_NF", src, "shot", 1, None) == "shot.png")
lbl = {"enabled": True, "mode": "prefix", "text": "A", "use_index": True, "index": 3}
check("label prefix + idx",
      core.camera_file_name("PFM1_NF", src, "", 2, lbl) == f"A_03__PFM1_NF__{ts}.png")
lbl = {"enabled": True, "mode": "suffix", "text": "", "use_index": True, "index": 12}
check("label suffix idx only",
      core.camera_file_name("PFM1_NF", src, "", 2, lbl) == f"PFM1_NF__{ts}__12.png")
lbl = {"enabled": False, "mode": "prefix", "text": "A"}
check("label off", core.camera_file_name("PFM1_NF", src, "", 2, lbl) == f"PFM1_NF__{ts}.png")
check("planned count", core.planned_output_count(None, True, ["a"]) == 2
      and core.planned_output_count(None, False, ["a"]) == 1
      and core.planned_output_count([1, 2], False, []) == 2)
check("sanitize", core.sanitize_folder_name(' a:b*c  d ') == "a_b_c d"
      and core.sanitize_folder_name("..") == "")
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    check("one output writes straight into the destination",
          core.ensure_output_path(d, "name", 1) == d)
    check("several outputs make the named folder",
          core.ensure_output_path(d, "run 1", 3) == d / "run 1" and (d / "run 1").is_dir())
    check("no name, no folder", core.ensure_output_path(d, "", 3) == d)

copier = core.ArchiveCopier(lambda m: None)
copier.label_settings["C"] = {"index": 4}
copier.bump_label_index("C")
copier.bump_label_index("new")
check("label counter moves on", copier.label_for("C")["index"] == 5
      and copier.label_for("new")["index"] == 2)

print("presets file")
check("presets live beside the program", core.presets_path().parent == H.PROGRAM)
fac = core.factory_presets()
check("built-ins start with All cameras", list(fac)[0] == "All cameras"
      and set(fac["LT1"]["cams"]) == set(core.CAM_CATEGORIES["LT1"]))
fac["LT1"]["cams"].append("X")
check("built-ins are a private copy", "X" not in core.CAM_CATEGORIES["LT1"])
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "custom_presets.json"
    check("missing file → built-ins", core.load_presets(p) == core.factory_presets())
    data = {"Mine": {"cams": ["PFM1_NF"], "mons": [2]}, "LT1": {"cams": [], "mons": None}}
    check("save ok", core.save_presets(data, p) is None)
    check("v2 round trip keeps the order", list(core.load_presets(p)) == ["Mine", "LT1"])
    p.write_text('{"Old": {"cams": ["SAM18_NF"]}}', encoding="utf-8")
    got = core.load_presets(p)
    check("v1 merges over the built-ins", "Old" in got and "All cameras" in got
          and list(got)[-1] == "Old")
    p.write_text("not json", encoding="utf-8")
    check("broken file → built-ins", core.load_presets(p) == core.factory_presets())

print("station rules")
l3bt = core.CAM_CATEGORIES["L3BT"][0]
check("L3BT is archive-only", not core.cam_available(l3bt, "window", "L3-VIS02", "VIS-02")
      and core.cam_available(l3bt, "cpva", "L3-VIS02", "VIS-02"))
check("SAM13_NF is shown at VIS02, not at VIS01",
      core.cam_available("SAM13_NF", "window", "L3-VIS02", "VIS-02")
      and not core.cam_available("SAM13_NF", "window", "L3-VIS01", "VIS-01"))
check("the office box follows the Layout box",
      core.cam_available("SAM13_NF", "window", core.CZOW_STATION, "VIS-02")
      and not core.cam_available("SAM13_NF", "window", core.CZOW_STATION, "VIS-01"))
check("hostname spelling: L3-OPR01 is OPR1",
      core.cam_available("OM1_FF", "window", "L3-OPR01", "OPR-01"))
check("a camera listed nowhere is allowed everywhere",
      core.cam_available("PTM4_FF", "window", "L3-OPR1", "OPR-01"))
check("an alias counts as its archive name (PTM5_2w_NF = PTM52w_NF, VIS01 only)",
      not core.cam_available("PTM5_2w_NF", "window", "L3-OPR1", "OPR-01")
      and core.cam_available("PTM5_2w_NF", "window", "L3-VIS01", "VIS-01"))
check("greyed tooltip names the station",
      core.cam_tooltip("SAM13_NF", False) == "Available on: L3-VIS02")

H.finish()
