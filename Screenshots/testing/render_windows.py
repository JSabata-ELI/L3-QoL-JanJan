"""Picture every window of Screenshots into testing/render_*.png.

Nothing appears on screen (qt_harness: minimal platform with the real fonts),
so the pictures show true text sizes without taking the keyboard focus.
The preset file and the sample pictures live in a temporary folder.

Run:  python testing/render_windows.py
"""
import shutil
import tempfile
from pathlib import Path

import qt_harness as H

app = H.app()

import numpy as np                      # noqa: E402
from PIL import Image                   # noqa: E402
from PySide6.QtCore import QPoint, Qt   # noqa: E402
from PySide6.QtGui import QPainter, QPixmap, QColor  # noqa: E402
from PySide6.QtTest import QTest        # noqa: E402

import ssq_common as ui                 # noqa: E402
import ssq_core as core                 # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="ssq_render_"))
core.presets_path = lambda: TMP / "custom_presets.json"
import main as M                        # noqa: E402

OUT = H.HERE


def shot(w, name):
    H.pump(10)
    pm = w.grab()
    pm.save(str(OUT / f"render_{name}.png"))
    print("saved", name, pm.width(), "x", pm.height())


win = M.ScreenshotsWindow()
win.resize(1440, 820)
win.show()
H.pump(10)
win.toggle_preset("PLFE")
win._set_cam("SAM18_NF", True)
win._update_name_label()
win._auto_status.setText("cycle 3/10")
win.log("[AUTO] cycle 3 @ 10-00-10  copied 11/11  spread 0.0ms–1.2s  lag 0.4s  took 0.9s")
shot(win, "main")

# sample pictures: a grey ramp, a colour picture, a 16-bit frame
pics = TMP / "pics"
pics.mkdir()
paths = []
yy, xx = np.mgrid[0:240, 0:320]
for i in range(6):
    blob = np.exp(-(((xx - 100 - 20 * i) / 40.0) ** 2 + ((yy - 120) / 30.0) ** 2))
    arr = (blob * 220 + 20).astype(np.uint8)
    p = pics / f"PFM{i}_NF__2026-10-16__10-00-0{i}.png"
    if i == 1:
        Image.fromarray(np.dstack([arr, arr[::-1], arr[:, ::-1]]), "RGB").save(p)
    else:
        Image.fromarray(arr, "L").save(p)
    paths.append(p)

pv = win._new_preview(list(paths), cycle_mode=True)
pv.resize(1100, 780)
pv.set_cycle_history([{"cycle": 1, "ts": "10:00:00", "files": paths[:3]},
                      {"cycle": 2, "ts": "10:01:00", "files": paths}])
pv.show_cycle(2)
pv.set_cycle_status("cycle 2 — 6/6 cameras  spread 0.0ms–1.1s")
pv.show()
shot(pv, "preview_cycle")
pv._palette_cb.setCurrentText("Rainbow")
pv._auto_c.setChecked(True)
shot(pv, "preview_rainbow_auto")

pv2 = win._new_preview(list(paths[:2]))
pv2.show()
shot(pv2, "preview_plain")

pv._open_zoom_window(paths[0])
H.pump(10)
zw = pv._zoom_windows[-1]
for b in zw.tool_group.buttons():
    if b.property("tool") == "rect":
        b.setChecked(True)
QTest.mousePress(zw.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(60, 60))
QTest.mouseMove(zw.canvas, QPoint(160, 140))
QTest.mouseRelease(zw.canvas, Qt.LeftButton, Qt.NoModifier, QPoint(160, 140))
QTest.mousePress(zw.canvas, Qt.RightButton, Qt.NoModifier, QPoint(20, 20))
QTest.mouseRelease(zw.canvas, Qt.RightButton, Qt.NoModifier, QPoint(220, 200))
shot(zw, "zoom")

pm = win._open_preset_manager()
pm.list.setCurrentRow(7)
shot(pm, "preset_manager")

win._clear_all_selections()
for c in ("PFM1_NF", "PFM4_NF", "SAM18_NF"):
    win._set_cam(c, True)
win.copier.label_settings["PFM1_NF"] = {"enabled": True, "mode": "prefix", "text": "ref",
                                        "use_index": True, "index": 4}
ld = win.on_labels()
shot(ld, "labels")
win._last_out_dir = TMP
dd = win.on_detail()
dd.text.setPlainText("Alignment check after the PFM8 move.")
shot(dd, "detail")

# drawn icons on a light panel and on the checked blue, 20 and 40 px
names = list(ui._RECIPES)
sheet = QPixmap(60 + 70 * len(names), 150)
sheet.fill(QColor(ui.PANEL))
p = QPainter(sheet)
for i, n in enumerate(names):
    ico = ui.icon(n)
    x = 20 + 70 * i
    p.drawPixmap(x, 10, ico.pixmap(20, 20))
    p.drawPixmap(x, 40, ico.pixmap(40, 40))
    p.fillRect(x - 4, 90, 48, 48, QColor(ui.ACCENT))
    p.drawPixmap(x, 94, ico.pixmap(40, 40, ui.QIcon.Normal, ui.QIcon.On))
    p.drawText(x, 148, n)
p.end()
sheet.save(str(OUT / "render_icons.png"))
print("saved icons")

for w in (zw, pv, pv2, pm, ld, dd):
    w.close()
win.close()
H.pump()
shutil.rmtree(TMP, ignore_errors=True)   # this script's own temporary folder
