"""Build the real window and photograph every tab, so they can be LOOKED at.

Run:  python testing/render_window.py
Writes: testing/tab_watch.png, tab_values.png, tab_areas.png, tab_alarm.png

Rendered on the real Windows platform plugin — offscreen has no fonts and lies
about text size.

The settings are redirected to a throw-away copy first. A render harness must
never be able to write the operator's own presets.json.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QEventLoop, QTimer                   # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

app = QApplication.instance() or QApplication([])

import ann_core as C                                            # noqa: E402
import ann_ui as U                                              # noqa: E402

_tmp = tempfile.mkdtemp(prefix="announcer_render_")
_copy = Path(_tmp) / "presets.json"
_real = C.program_dir() / C.CONFIG_NAME
if _real.exists():
    _copy.write_bytes(_real.read_bytes())
C.config_path = lambda: _copy
print(f"settings redirected to {_copy}")

U.install_app_look(app)

import main                                                     # noqa: E402

win = main.AnnouncerWindow()
win.resize(1500, 900)
win.show()


def settle(ms):
    """Let the timers run — results come back through signals, so a loop of
    processEvents() is not enough; a real event loop has to turn."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


settle(2500)          # one archiver pass and one grab pass

names = ["tab_watch", "tab_values", "tab_areas", "tab_alarm"]
for i in range(win._tabs.count()):
    win._tabs.setCurrentIndex(i)
    settle(600)
    out = HERE / f"{names[i] if i < len(names) else f'tab_{i}'}.png"
    win.grab().save(str(out))
    print(f"wrote {out.name}  ({win._tabs.tabText(i)})")

win.close()
