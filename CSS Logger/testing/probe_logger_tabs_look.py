"""
Look: the setting tabs in the main window's tab row.

Renders the window with three setting tabs (nothing on screen: minimal platform
with real fonts) and saves the top strip to testing/_out/logger_tabs_row.png,
plus the same with Spectra on screen (the setting tabs must be gone).

Run:  python testing/probe_logger_tabs_look.py
"""
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
_TMP = tempfile.mkdtemp(prefix="css_logger_tabs_look_")
os.environ["CSS_LOGGER_TABS_FILE"] = os.path.join(_TMP, "logger_tabs.json")

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
OUT = _HERE / "_out"
OUT.mkdir(exist_ok=True)

import cpva_core
import main as app_main
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

cpva_core.cpva_fetch_samples = lambda *a, **k: []
app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
for fn in ("save_config", "save_presets", "save_condition_presets", "save_custom_pvs"):
    setattr(app_main, fn, lambda *a, **k: None)

app = QApplication.instance() or QApplication(sys.argv)
app_main.install_app_look(app)
win = app_main.CPVASuiteWindow()
win.resize(1600, 900)
# The minimal platform puts nothing on the real screen; showing is what lays the
# tab row out. Never takes the keyboard focus either way.
from PySide6.QtCore import Qt
win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
win.show()
lt = win._logger_tabs
lt.new_tab(name="Custom PVs")
lt.new_tab(name="Operation + ramping")
lt._tabs.setCurrentIndex(1)
for _ in range(20):
    app.processEvents()
corner = win._main_tabs.cornerWidget()
chrome = lt._tabs.chrome
print("corner", corner.geometry(), corner.isVisible(), "chrome", chrome.geometry(),
      chrome.isVisible(), chrome.parentWidget(), "bar", lt._tabs.tabBar().count(),
      lt._tabs.tabBar().geometry())
pm = win.grab(QRect(0, 0, 1600, 60))
pm.save(str(OUT / "logger_tabs_row.png"))
win._main_tabs.setCurrentIndex(1)
for _ in range(20):
    app.processEvents()
win.grab(QRect(0, 0, 1600, 60)).save(str(OUT / "logger_tabs_row_spectra.png"))
print("saved", OUT / "logger_tabs_row.png")
win.close()
