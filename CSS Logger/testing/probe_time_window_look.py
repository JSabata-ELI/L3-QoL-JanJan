"""
Renders the Start/End time dialog on both tabs, with real fonts and nothing on
screen, and checks that switching tabs never changes the window size.

Run:  python probe_time_window_look.py [output folder]
Writes tw_relative.png and tw_absolute.png (default: the system temp folder).
"""
import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta

os.environ["QT_QPA_PLATFORM"] = "minimal:enable_fonts"

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import main as app_main                                     # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

_app = QApplication.instance() or QApplication([])
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.gettempdir())

now = datetime.now()
dlg = app_main.TimeWindowDialog(None, now - timedelta(minutes=20, seconds=13), now)
dlg.show()
_app.processEvents()
sizes = {"start": dlg.size()}

dlg.grab().save(str(out / "tw_relative.png"))
for which in ("from", "to"):
    dlg._sides[which]["tabs"].setCurrentIndex(0)
_app.processEvents()
sizes["absolute"] = dlg.size()
dlg.grab().save(str(out / "tw_absolute.png"))
for which in ("from", "to"):
    dlg._sides[which]["tabs"].setCurrentIndex(1)
dlg._sides["from"]["rel"]["Days"].setValue(1234)
_app.processEvents()
sizes["long text"] = dlg.size()

same = len({(s.width(), s.height()) for s in sizes.values()}) == 1
print({k: (v.width(), v.height()) for k, v in sizes.items()})
print("PASS  one window size for every tab" if same else "FAIL  the window size changes")
print("written to", out)
sys.exit(0 if same else 1)
