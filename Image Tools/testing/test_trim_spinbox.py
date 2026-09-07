"""Render check: the Shot Finder target/tolerance boxes must show their whole value.

A tolerance of 2000 J used to be painted as "2000." in a 50 px box and read like a
2, so a ±2000 J band silently matched every shot of the day. This renders the real
widget at its real width and asserts the painted text is not clipped.

Run:  python testing/test_trim_spinbox.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")   # offscreen has no fonts
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel  # noqa: E402
from PySide6.QtGui import QFontMetrics                                    # noqa: E402

import sf_t                                                              # noqa: E402

app = QApplication([])
# The same style + palette the program runs with. Without it Windows 11's dark
# style gives the spin arrows most of the box and the number is invisible even
# when it is short — the test would then fail on the host, not on the widget.
app.setStyle("Fusion")
app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                  "QLabel { background: transparent; }")

CASES = [0.5, 2.0, 20.0, 2000.0, 0.0, 12.345]

row = QWidget()
lay = QHBoxLayout(row)
boxes = []
for v in CASES:
    sb = sf_t._TrimSpinBox()
    sb.setRange(0.0, 1e9)
    sb.setDecimals(3)
    sb.setValue(v)
    sb.setFixedWidth(56)
    lay.addWidget(QLabel(f"{v:g} →"))
    lay.addWidget(sb)
    boxes.append((v, sb))

row.show()
row.resize(row.sizeHint())
app.processEvents()

# Measured only after the layout has run: before show() the inner line edit still
# reports the sizeHint width and every case "fits".
bad = []
for v, sb in boxes:
    shown = sb.lineEdit().text()
    fm = QFontMetrics(sb.lineEdit().font())
    room = sb.lineEdit().width()
    need = fm.horizontalAdvance(shown)
    ok = need <= room
    print(f"value {v:>10g}  text {shown!r:>10}  needs {need} px, has {room} px  "
          f"{'ok' if ok else 'CLIPPED'}")
    if not ok:
        bad.append((v, shown, need, room))
out = HERE / "trim_spinbox.png"
row.grab().save(str(out))
print(f"screenshot: {out}")

if bad:
    print("FAILED — clipped:", bad)
    sys.exit(1)
print("PASS — every value fits its box")
