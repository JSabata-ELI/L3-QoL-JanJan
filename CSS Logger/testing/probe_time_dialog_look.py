"""Grab the Start/End Time window (Absolute and Relative) into testing/_out,
nothing on screen, to check the quick-time buttons are whole.

    python testing/probe_time_dialog_look.py
"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("QT_SCALE_FACTOR", "1.5")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as css  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
css.install_app_look(app)
OUT = os.path.join(HERE, "_out")
os.makedirs(OUT, exist_ok=True)

now = datetime.now()
d = css.TimeWindowDialog(None, now - timedelta(hours=1), now)
d.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
for name, idx in (("time_dialog_absolute", 0), ("time_dialog_relative", 1)):
    for side in ("from", "to"):
        d._sides[side]["tabs"].setCurrentIndex(idx)
    d.show()
    app.processEvents()
    d.grab().save(os.path.join(OUT, name + ".png"))
    for b in d._sides["to"]["abs_btns"]:
        if b.width() < b.sizeHint().width():
            print("SQUEEZED", b.text(), b.width(), "<", b.sizeHint().width())
print("saved to", OUT, "size", d.width(), "x", d.height())
d.hide()
