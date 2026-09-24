"""Render every drawn icon on a house button, so they can be LOOKED at.

Run:  python testing/render_icons.py
Writes: testing/icons.png

Rendered on the real Windows platform plugin — offscreen has no fonts and lies
about text size. Three rows: a normal button, a red one, and a blue one, because
an icon has to be legible in all three and Qt will not invent that for us.
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import (QApplication, QGridLayout, QLabel,  # noqa: E402
                               QVBoxLayout, QWidget)

app = QApplication.instance() or QApplication([])

import ann_ui as U            # noqa: E402

U.install_app_look(app)

w = QWidget()
w.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")
lay = QVBoxLayout(w)
lay.addWidget(QLabel("Every icon, on the three kinds of button:"))
grid = QGridLayout()
lay.addLayout(grid)

for col, name in enumerate(U.ICON_NAMES):
    grid.addWidget(U.button(name, icon_name=name), 0, col)
    grid.addWidget(U.button(name, icon_name=name, danger=True), 1, col)
    grid.addWidget(U.button(name, icon_name=name, primary=True), 2, col)
    b = U.button(name, icon_name=name)
    b.setEnabled(False)
    grid.addWidget(b, 3, col)

lay.addWidget(QLabel("Row 4 is disabled — it must be faint but still there."))
w.resize(1500, 220)
w.show()
app.processEvents()
out = HERE / "icons.png"
w.grab().save(str(out))
print(f"wrote {out}")
