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

app = QApplication([])
app.setStyle("Fusion")
app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                  "QLabel { background: transparent; }")

w = sf_t.ShotFinderWidget()
w._pv_cfg = [
    {"col": "sbw4", "target": 20.0, "tol": 2.0,    "filter": True},
    {"col": "ptm1", "target": 20.0, "tol": 2000.0, "filter": True},
    {"col": "pcm2", "target": 10.0, "tol": 0.0,    "filter": False},
]
w._rebuild_pv_rows()
app.processEvents()

box = w._pv_container
box.resize(275, box.sizeHint().height())
app.processEvents()
out = HERE / "sf_pv_row.png"
box.grab().save(str(out))
print(f"screenshot: {out}")

for r in w._pv_rows:
    print(f"{r['col']:>10}  target={r['target_sb'].lineEdit().text():>8}  "
          f"tol={r['tol_sb'].lineEdit().text():>8}  "
          f"marked={'yes' if r['tol_sb'].styleSheet() else 'no'}")
