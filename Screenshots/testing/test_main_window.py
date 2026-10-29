"""The Qt main window, its side windows and a real Copy / Auto run.

Runs without anything on screen (qt_harness: minimal platform). Message boxes
are answered by stubs, the preset file and the archive are temporary folders,
so nothing outside %TEMP% is written.

Pinned:
  * a camera that sits in two sections is one camera: both boxes follow it;
  * section "All", presets on/off, Clear all, search, window-mode greying;
  * the Selected cameras box shows every entry and is tall enough for them;
  * Preset Manager: new, rename, reorder, remove — saved to the file;
  * Labels and Detail write what they say;
  * the preview: grid, NI palettes grey the tones, Auto greys its slider,
    the zoom window's drawing reaches the saved <name>_annotated.png;
  * Copy and a 2-cycle Auto run on a fake archive copy the frames and fill
    the preview (cycle 1 = newest frames, the clock offset is measured).

Run:  python testing/test_main_window.py
"""
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import qt_harness as H

app = H.app()
check = H.check

import numpy as np                      # noqa: E402
from PIL import Image                   # noqa: E402
from PySide6.QtCore import QPoint, Qt   # noqa: E402
from PySide6.QtTest import QTest        # noqa: E402

import ssq_common as ui                 # noqa: E402
import ssq_core as core                 # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="ssq_test_"))
core.presets_path = lambda: TMP / "custom_presets.json"
asked = []
ui.ask_yes_no = lambda *a: (asked.append(a[1:]), True)[1]
ui.message = lambda *a: asked.append(a[1:])

import main as M                        # noqa: E402

win = M.ScreenshotsWindow()
win.resize(1440, 820)
win.show()
H.pump(10)


def wait_until(cond, timeout=15.0):
    t_end = time.time() + timeout
    while time.time() < t_end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


print("cameras")
check("title names the station", win._station_id in win.windowTitle())
two = [c for c, boxes in win._cam_boxes.items() if len(boxes) > 1]
check("some cameras sit in two sections", bool(two), str(two))
cam2 = two[0]
win._cam_boxes[cam2][0].click()
H.pump()
check("both boxes of a two-section camera follow one click",
      all(b.isChecked() for b in win._cam_boxes[cam2]) and win._cam_sel[cam2])
win._cam_boxes[cam2][1].click()
check("…and both untick from the other one",
      not any(b.isChecked() for b in win._cam_boxes[cam2]) and not win._cam_sel[cam2])

win._clear_all_selections()
win._cat_boxes["LT1"].click()
H.pump()
check("section All ticks the section", set(win._selected_cameras()) == set(core.CAM_CATEGORIES["LT1"]))
check("many outputs → Folder name", win._name_label.text() == "Folder name:")
n_lbl = win._sel_grid.count()
want = 2 + len(core.CAM_CATEGORIES["LT1"]) + 1      # Monitors: + All screens + Cameras: + cams
check("Selected cameras lists everything", n_lbl == want, f"{n_lbl} vs {want}")
check("Selected cameras box is tall enough",
      win._sel_scroll.height() >= win._sel_inner.sizeHint().height())
win._cat_boxes["LT1"].click()
check("section All unticks", not win._selected_cameras())

win.toggle_preset("LT2")
check("preset on ticks its cameras", set(win._selected_cameras()) >= set(core.CAM_CATEGORIES["LT2"]))
check("preset button marked on", win.preset_buttons["LT2"].property("presetOn") is True)
check("section of the preset opened", win._cam_sections["LT2"].expanded)
win.toggle_preset("LT2")
check("preset off unticks", not win._selected_cameras())
check("preset button marked off", win.preset_buttons["LT2"].property("presetOn") is False)

win.cam_search.setText("pfm1")
H.pump()
vis = [c for c, b in win._cat_widgets["LT1"].items() if not b.isHidden()]
check("search shows only matches", vis and all("pfm1" in c.lower() for c in vis), str(vis))
check("a section without matches is hidden", win._cam_sections["LT2"].isHidden())
win.cam_search.setText("")
H.pump()
check("clearing the search brings every section back",
      not any(s.isHidden() for s in win._cam_sections.values()))

win._src_window.setChecked(True)
H.pump()
l3bt = core.CAM_CATEGORIES["L3BT"][0]
box = win._cat_widgets["L3BT"][l3bt]
check("window mode greys L3BT cameras", not box.isEnabled())
check("a greyed camera says where it is available", box.toolTip().startswith("Available on"))
win._src_cpva.setChecked(True)
H.pump()
check("archive mode enables them again", box.isEnabled())

print("monitors")
if len(win._mon_boxes) >= 2:
    idx = sorted(win._mon_boxes)[0]
    win._mon_boxes[idx].click()
    check("a monitor box turns All screens off",
          not win._all_screens_cb.isChecked() and win._selected_monitors() == [idx])
    win._all_screens_cb.click()
    check("All screens clears the single monitors",
          win._all_screens_cb.isChecked() and win._selected_monitors() is None)
else:
    check("monitor grid built", len(win._mon_boxes) >= 1 or win._station_id == core.CZOW_STATION)

print("preset manager")
pm = win._open_preset_manager()
H.pump()
pm.on_new()
pm.name_edit.setText("TestP")
pm.cam_boxes["PFM1_NF"].setChecked(True)
pm.cam_boxes["SAM18_NF"].setChecked(True)
pm.on_save()
check("new preset saved", win._presets.get("TestP", {}).get("cams") == ["PFM1_NF", "SAM18_NF"])
check("new preset got a button", "TestP" in win.preset_buttons)
check("preset file written", (TMP / "custom_presets.json").exists())
pm.name_edit.setText("TestQ")
pm.on_save()
check("rename keeps the place", list(win._presets)[-1] == "TestQ" and "TestP" not in win._presets)
pm.list.model().moveRow(pm.list.model().index(pm.list.count() - 1, 0).parent(),
                        pm.list.count() - 1, pm.list.model().index(0, 0).parent(), 0)
H.pump()
check("drag to the top reorders the presets", list(win._presets)[0] == "TestQ",
      str(list(win._presets)[:3]))
check("buttons follow the new order", list(win.preset_buttons)[0] == "TestQ")
pm.list.setCurrentRow(0)
pm.on_remove()
check("remove", "TestQ" not in win._presets and "TestQ" not in win.preset_buttons)
pm.search_edit.setText("sam 18")
vis = [c for c, b in pm.cam_boxes.items() if not b.isHidden()]
check("manager search: every word, any order", vis and all("sam18" in c.lower() for c in vis), str(vis))
pm.close()

print("labels / detail")
win._clear_all_selections()
win._set_cam("PFM1_NF", True)
win._set_cam("PFM4_NF", True)
ld = win.on_labels()
H.pump()
ld.rows["PFM1_NF"]["enabled"].setChecked(True)
ld.rows["PFM1_NF"]["text"].setText("ref")
ld.rows["PFM1_NF"]["use_index"].setChecked(True)
ld._save_and_close()
s1 = win.copier.label_for("PFM1_NF")
check("labels saved", s1.get("enabled") and s1.get("text") == "ref" and s1.get("use_index"))
win._last_out_dir = TMP
dd = win.on_detail()
QTest.keyClicks(dd.text, "typed at once")   # before the file read has answered
time.sleep(0.3)
H.pump(10)
check("typing right after opening survives the late file read",
      dd.text.toPlainText() == "typed at once", repr(dd.text.toPlainText()))
dd.text.setPlainText("note line")
dd._save()
check("detail note written", (TMP / "notes.txt").read_text(encoding="utf-8") == "note line")
dd.close()

print("preview")
pics = TMP / "pics"
pics.mkdir()
rng = np.random.default_rng(1)
paths = []
for i in range(5):
    p = pics / f"CAM{i}__2026-10-16__10-00-0{i}.png"
    Image.fromarray(rng.integers(0, 256, (60, 80), dtype=np.uint8), "L").save(p)
    paths.append(p)
pv = win._new_preview(list(paths))
pv.show()
H.pump(10)
check("every picture has a cell", len(pv._cells) == 5)
pv._palette_cb.setCurrentText("Binary")
check("NI palette greys the tone controls",
      not pv._gamma_s.isEnabled() and not pv._contrast_s.isEnabled() and not pv._auto_c.isEnabled())
pv._palette_cb.setCurrentText("Original")
pv._auto_c.setChecked(True)
H.pump(5)
check("Auto contrast greys its slider", not pv._contrast_s.isEnabled() and pv._bright_s.isEnabled())
pv._reset_contrast()
check("reset turns Auto off and frees the slider",
      not pv._auto_c.isChecked() and pv._contrast_s.isEnabled() and pv._contrast_s.value() == 0)

pv._open_zoom_window(paths[0])
H.pump(10)
zw = pv._zoom_windows[-1]
for b in zw.tool_group.buttons():
    if b.property("tool") == "rect":
        b.setChecked(True)
QTest.mousePress(zw.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(5, 5))
QTest.mouseMove(zw.canvas, QPoint(30, 25))
QTest.mouseRelease(zw.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(30, 25))
QTest.mousePress(zw.canvas, Qt.RightButton, Qt.NoModifier, QPoint(2, 2))
QTest.mouseRelease(zw.canvas, Qt.RightButton, Qt.NoModifier, QPoint(50, 40))
check("right drag sets the crop", zw._crop_rect == (2, 2, 50, 40), str(zw._crop_rect))
zw._apply_changes()
H.pump()
st = pv._img_states.get(paths[0])
check("Apply hands the drawing back", st is not None
      and np.array(st["overlay"])[:, :, 3].max() > 0)
pv._do_keep()
H.pump()
ann = pics / f"{paths[0].stem}_annotated.png"
check("Save writes the annotated picture", ann.exists())
if ann.exists():
    with Image.open(ann) as im:
        check("…cropped to the right-button box", im.size == (48, 38), str(im.size))

print("copy + auto on a fake archive")
arch = TMP / "archive"
core.NETWORK_ROOT = arch
cams = {"PFM1_NF": "C03-013-PFM1NF-_-IMG", "SAM18_NF": "C03-069-SAM18NF-_-IMG"}
stop_writer = threading.Event()


def cam_dir(name: str) -> Path:
    d = datetime.now(timezone.utc)
    p = arch / str(d.year) / str(d.month) / str(d.day) / str(d.hour) / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def writer():
    while not stop_writer.is_set():
        ns = time.time_ns()
        for folder in cams.values():
            Image.fromarray(np.full((20, 30), (ns // 10**8) % 256, np.uint8), "L").save(
                cam_dir(folder) / f"img_{ns}.png")
        time.sleep(0.5)


wt = threading.Thread(target=writer, daemon=True)
wt.start()
time.sleep(1.2)
dest = TMP / "dest"
win.dest_edit.setText(str(dest))
win._clear_all_selections()
win._all_screens_cb.click()          # no screenshots — cameras only
for c in cams:
    win._set_cam(c, True)
win._update_name_label()
win.run_name_edit.setText("run1")
win.on_copy()
check("Copy finishes", wait_until(lambda: not win._busy and win._copy_btn.isEnabled()))
got = sorted(p.name for p in (dest / "run1").glob("*.png"))
check("Copy wrote one frame per camera", len(got) == 2
      and any(n.startswith("SAM18_NF__") for n in got), str(got))
check("PFM1 label applied", any(n.startswith("ref_01__PFM1_NF__") for n in got), str(got))
check("label counter moved on", win.copier.label_for("PFM1_NF").get("index") == 2)


def copy_preview_open():
    import shiboken6
    return any(shiboken6.isValid(w) and sorted(p.name for p in w._paths) == got
               for w in win._previews)


check("Copy opened a preview of exactly those files", wait_until(copy_preview_open, 3))

win.run_name_edit.setText("auto")
win._auto_interval.setValue(5)
win._auto_cycles.setValue(2)
t0 = time.time()
win._toggle_auto_copy()
check("auto running", win._auto_copy_active and win._auto_btn.text() == "Stop auto")
done = wait_until(lambda: not win._auto_copy_active, 20)
check("auto stops by itself after 2 cycles", done, f"{time.time() - t0:.1f}s")
hist = win._auto_cycle_history
check("two cycles recorded", [e["cycle"] for e in hist] == [1, 2],
      str([e["cycle"] for e in hist]))
check("each cycle copied both cameras", all(len(e["files"]) == 2 for e in hist),
      str([len(e["files"]) for e in hist]))
check("clock offset measured on cycle 1", win._auto_offset_ns is not None)
pv_auto = win._auto_preview
check("live preview shows the last cycle",
      pv_auto is not None and pv_auto.shown_cycle == 2 and len(pv_auto._cells) == 2)
check("button back to Start", win._auto_btn.text() == "Start auto")
stop_writer.set()

for w in list(win._previews):
    try:
        w.close()
    except Exception:
        pass
win.close()
H.pump()
shutil.rmtree(TMP, ignore_errors=True)   # this test's own temporary folder
H.finish()
