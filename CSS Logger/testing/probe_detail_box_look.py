"""
Probe: the "Detail:" box in the sidebar (was "Avg to") — does its text ("all"
at 0, then the numbers up to 20000) fit inside it, and how does the row look?
("all samples" was tried first: cut to "nples"; at 90 px "20000" was cut too.)
The printed widths are a hint only — at 90 px they said "fits" while the
picture showed "20000" cut. LOOK AT THE PNGs.

Nothing appears on screen (minimal platform with real fonts). Saves
testing/_out/detail_box_<value>.png and prints the text width against the
room the box's text field has.

Run:  python testing/probe_detail_box_look.py
"""
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
_HERE = pathlib.Path(__file__).resolve().parent
_APP = _HERE.parent
sys.path.insert(0, str(_APP))
os.chdir(_APP)
OUT = _HERE / "_out"
OUT.mkdir(exist_ok=True)

from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox

import main as M

M.CSSLoggerWidget._maybe_autostart_live = lambda self: None
M.CSSLoggerWidget._save_runtime_state = lambda self, *a, **k: None

app = QApplication.instance() or QApplication(sys.argv)
w = M.CSSLoggerWidget()
w.resize(1800, 1000)
w._autoload_timer.stop()
spin = w._avg_target_spin
row = spin.parentWidget()
ok = True
for value in (0, 2000, 20000):
    spin.blockSignals(True)
    spin.setValue(value)
    spin.blockSignals(False)
    # The room is what the style gives the text field inside the fixed-width
    # box (the line edit's own width is not laid out on a hidden window).
    opt = QStyleOptionSpinBox()
    spin.initStyleOption(opt)
    room = spin.style().subControlRect(
        QStyle.CC_SpinBox, opt, QStyle.SC_SpinBoxEditField, spin).width() - 4
    text = spin.specialValueText() if value == spin.minimum() else spin.text()
    need = spin.fontMetrics().horizontalAdvance(text)
    fits = need <= room
    ok &= fits
    print(f"value {value}: shows {text!r}, text {need}px, room {room}px, "
          f"{'fits' if fits else 'CUT'}")
    spin.grab().save(str(OUT / f"detail_box_{value}.png"))
    row.grab().save(str(OUT / f"detail_row_{value}.png"))
print("all fit" if ok else "SOMETHING IS CUT")
