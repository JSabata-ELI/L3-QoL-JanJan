"""Render check: one PV row of the Shot Finder panel.

Shows the target/tolerance boxes at their real width inside the real row, and the
amber mark that appears when the tolerance is as wide as the target (the ±2000 J
case that matched every shot of the day).

Run:  python testing/test_sf_pv_row.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication          # noqa: E402

import sf_t                                          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                  "QLabel { background: transparent; }")

w = sf_t.ShotFinderWidget()
# One row per band mode, a long typed-in channel name, the ±2000 J case, a
# swapped range and an unticked row.
w._pv_cfg = [
    {"col": "sbw4", "target": 20.0, "tol": 2.0,    "filter": True},
    {"col": "ptm1", "target": 20.0, "tol": 2000.0, "filter": True},
    {"col": "pcm2", "mode": "pct", "target": 20.0, "pct": 5.0, "filter": True},
    {"col": "L3-OM1FF-PM312:EnergyLongChannelName", "mode": "range",
     "lo": 22.0, "hi": 18.0, "filter": True},
    {"col": "sbw4", "mode": "above", "lo": 19.5, "filter": True},
    {"col": "ptm1", "mode": "below", "hi": 21.25, "filter": True},
    {"col": "pcm2", "target": 10.0, "tol": 0.0,    "filter": False},
]
w._rebuild_pv_rows()
app.processEvents()

box = w._pv_container
# The panel's real room: 280 px scroll area − 16 px scrollbar − 4 px margin.
# Shown as a real window (a never-shown widget grabs with stale pixels), placed
# off to the side and never taking the keyboard focus.
box.setParent(None)
box.setAttribute(sf_t.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
box.setWindowFlags(sf_t.Qt.WindowType.Tool
                   | sf_t.Qt.WindowType.FramelessWindowHint
                   | sf_t.Qt.WindowType.WindowDoesNotAcceptFocus)
box.setStyleSheet("QWidget { background: #eef6f6; color: #111; }")
box.resize(260, box.sizeHint().height())
box.move(-2000, 0)
box.show()
for _ in range(5):
    app.processEvents()
out = HERE / "sf_pv_row.png"
box.grab().save(str(out))
print(f"screenshot: {out}")

# Nothing may stick out of the panel: every visible child of a row ends inside it.
over = []
for r in w._pv_rows:
    row = r["chk"].parentWidget()
    for wdg in row.findChildren(sf_t.QWidget):
        if wdg.isVisibleTo(row):
            right = wdg.mapTo(box, wdg.rect().topRight()).x()
            if right > box.width():
                over.append((r["col"], type(wdg).__name__, right))
for r in w._pv_rows:
    cfg = r["cfg"]
    marked = [n for n in ("tol_sb", "pct_sb", "lo_sb", "hi_sb")
              if r[n].styleSheet()]
    print(f"{r['col'][:22]:>22}  {cfg['mode']:>5}  "
          f"band={sf_t._band_text(cfg, 'J'):<16} marked={marked or '-'}")
print("overflow:", over or "none")
