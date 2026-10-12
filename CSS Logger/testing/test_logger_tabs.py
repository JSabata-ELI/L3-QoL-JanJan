"""
Setting tabs in the CSS Logger (logger_tabs.py).

Asked for 12.10.2026: tabs like Image Tools, one per setting (a preset, a
hand-picked set of channels, a preset plus extra channels), updating in the
background and up to "now" as soon as one is opened.

What this pins:

  * a first start (no tabs file) is one tab, named after its preset, and it
    starts exactly as the CSS Logger always did,
  * "+" opens a tab that is a copy of the one on screen, and makes it current,
  * only the tab on screen is awake: the others are suspended, and so is every
    tab while the Spectra tab is on screen,
  * a suspended tab in Live asks the archiver every HIDDEN_LIVE_POLL_MS, and
    showing it again catches it up at once,
  * the tabs, their names, channels, conditions and windows survive a restart,
    and the ones not on screen are built in the background, suspended,
  * a tab can be renamed and closed, and the last one cannot be closed.

Headless (offscreen), archive faked, tabs file in a temporary folder.

Run:   python testing/test_logger_tabs.py
"""
import gc
import os
import pathlib
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
_TMP = tempfile.mkdtemp(prefix="css_logger_tabs_")
os.environ["CSS_LOGGER_TABS_FILE"] = os.path.join(_TMP, "logger_tabs.json")
os.environ["APPDATA"] = _TMP
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
_APP = _HERE.parent
sys.path.insert(0, str(_APP))

import cpva_core
import logger_tabs
import main as app_main
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

_app = QApplication.instance() or QApplication([])
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def _fake(channel, start_ns, end_ns, timeout=10.0, count=None):
    n = 20
    step = max(1, (end_ns - start_ns) // n)
    return [{"time": int(start_ns) + i * step, "value": float(i % 5),
             "quality": "Original", "metaData": {"units": ""}} for i in range(n)]


cpva_core.cpva_fetch_samples = _fake
for fn in ("save_config", "save_presets", "save_condition_presets", "save_custom_pvs"):
    if hasattr(app_main, fn):
        setattr(app_main, fn, lambda *a, **k: None)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected


def _pump(seconds=0.0, cond=None):
    deadline = time.time() + seconds
    while True:
        _app.processEvents()
        if cond is not None and cond():
            return True
        if time.time() >= deadline:
            return cond() if cond is not None else True
        time.sleep(0.01)


def _window():
    return app_main.CPVASuiteWindow()        # never shown


def _close(win):
    win.close()
    win.deleteLater()
    _pump(0.3)
    gc.collect()


def test_first_start_and_new_tab():
    win = _window()
    lt = win._logger_tabs
    check("a first start is one tab", lt._tabs.count() == 1)
    lg = lt.active()
    check("named after its preset",
          lt._tabs.tabText(0) == lg._preset_combo.currentText() != "",
          lt._tabs.tabText(0))
    _pump(3.0, cond=lambda: lg._live_mode)
    check("it starts in Live, as the CSS Logger always did", lg._live_mode)

    lg2 = lt.new_tab()
    check("+ makes a second tab and shows it",
          lt._tabs.count() == 2 and lt.active() is lg2)
    check("the new tab is a copy of the one that was on screen",
          lg2._real_pv_names() == lg._real_pv_names())
    _pump(0.2)
    check("only the tab on screen is awake",
          lg._suspended and not lg2._suspended)
    check("a hidden tab in Live asks only every HIDDEN_LIVE_POLL_MS",
          lg._live_poll_ms() >= app_main.HIDDEN_LIVE_POLL_MS
          and lg2._live_poll_ms() < app_main.HIDDEN_LIVE_POLL_MS)

    # The Spectra tab on screen: every setting tab sleeps.
    win._main_tabs.setCurrentIndex(1)
    _pump(0.2)
    check("with Spectra on screen every setting tab is suspended",
          lg._suspended and lg2._suspended)
    win._main_tabs.setCurrentIndex(0)
    _pump(0.2)
    check("and back on the CSS Logger the current one wakes", not lg2._suspended)

    # Showing the first tab again catches it up: the next tick is now.
    ticks = []
    orig = lg._live_tick
    lg._live_tick = lambda: (ticks.append(1), orig())
    lt._tabs.setCurrentIndex(0)
    _pump(0.2)
    check("showing a Live tab again reads up to now at once",
          not lg._suspended and (ticks or not lg._live_timer.isActive()),
          f"{len(ticks)} ticks")
    lg._live_tick = orig

    # Rename and save.
    lt._start_rename(1)
    lt._rename_edit.setText("Ramping extra")
    lt._finish_rename()
    check("a tab can be renamed", lt._tabs.tabText(1) == "Ramping extra")
    lg2._stop_live()
    lg2._pv_list.takeItem(0)
    lt.save()
    saved = logger_tabs.TABS_PATH.read_text(encoding="utf-8")
    check("the tabs are written to the tabs file",
          "Ramping extra" in saved and '"live": false' in saved.replace(" ", "")
          .replace('"live":false', '"live": false'))
    _close(win)
    return lg2._real_pv_names()


def test_restart_brings_the_tabs_back(expected_pvs):
    old_first = logger_tabs.BACKGROUND_FIRST_MS
    logger_tabs.BACKGROUND_FIRST_MS = 200
    try:
        win = _window()
    finally:
        logger_tabs.BACKGROUND_FIRST_MS = old_first
    lt = win._logger_tabs
    check("both tabs come back, with their names",
          lt._tabs.count() == 2 and lt._tabs.tabText(1) == "Ramping extra",
          [lt._tabs.tabText(i) for i in range(lt._tabs.count())])
    _pump(3.0, cond=lambda: all(p.logger is not None for p in lt._pages()))
    pages = lt._pages()
    check("the tab not on screen is built in the background",
          all(p.logger is not None for p in pages))
    hidden = [p.logger for p in pages if p is not lt._current_page()]
    check("and it is suspended", hidden and all(lg._suspended for lg in hidden))
    second = pages[1].logger
    check("it has its own channels", second._real_pv_names() == expected_pvs,
          f"{second._real_pv_names()} vs {expected_pvs}")
    _pump(2.0)
    check("a tab saved standing still does not start Live", not second._live_mode)

    lt._close_page(pages[1])
    check("a tab can be closed", lt._tabs.count() == 1)
    lt._close_page(lt._pages()[0])
    check("the last tab cannot be closed", lt._tabs.count() == 1)
    _close(win)


def main() -> int:
    pvs = None
    try:
        pvs = test_first_start_and_new_tab()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("test_first_start_and_new_tab", False, f"raised {exc}")
    try:
        test_restart_brings_the_tabs_back(pvs)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        check("test_restart_brings_the_tabs_back", False, f"raised {exc}")
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
