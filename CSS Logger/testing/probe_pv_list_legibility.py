"""Is the text in the PV LIST readable?

The list box is painted white from code, so its text colour has to be painted
too. This renders the list as the program really shows it and reports how many
pixels inside it are not background -- i.e. whether the channel names were
drawn at all, and in what colour.

`install_app_look` is what makes this honest: without it the window is rendered
in whatever theme Windows is in, which is not the theme the program uses, and
the answer is about that theme instead of about the program.

Run:  set QT_QPA_PLATFORM=windows && python probe_pv_list_legibility.py
"""
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication

import main as css
from cpva_core import RAMPING_PV_MAP

OUT = HERE / "_out"
OUT.mkdir(exist_ok=True)

app = QApplication.instance() or QApplication(sys.argv)
css.install_app_look(app)

w = css.CSSLoggerWidget()
w.resize(1500, 900)
w.show()
app.processEvents()
if w._live_mode:
    w._stop_live("probe")
w._pv_list.clear()
for pv in RAMPING_PV_MAP.values():
    w._pv_list.addItem(pv)
app.processEvents()

lst = w._pv_list
img = lst.grab().toImage()
counts = Counter()
for y in range(6, img.height() - 6):
    for x in range(6, img.width() - 6):
        counts[img.pixelColor(x, y).name()] += 1
top = counts.most_common(6)
print("most common colours inside the list:")
for name, n in top:
    print(f"  {name}  {n}")
ink = sum(n for name, n in counts.items() if name != "#ffffff")
print(f"non-white pixels: {ink}")
lst.grab().save(str(OUT / "pv_list_plain.png"))
print("saved", OUT / "pv_list_plain.png")
assert ink > 500, "the channel names are invisible against the white list"
darkest = min(counts, key=lambda c: sum(int(c[i:i + 2], 16) for i in (1, 3, 5)))
print("darkest ink:", darkest)
assert sum(int(darkest[i:i + 2], 16) for i in (1, 3, 5)) < 300, \
    "nothing dark enough to read was drawn"
print("ok")
