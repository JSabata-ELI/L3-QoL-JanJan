"""Shared start for the Screenshots checks.

Puts the program folder on sys.path and starts Qt WITHOUT anything on screen:
`minimal:enable_fonts` has the real Windows fonts (so text sizes are true) and
draws nothing, so no window can take the keyboard focus. Set
SSQ_QPA=offscreen to fall back if the minimal plugin is missing.

    import qt_harness as H
    app = H.app()
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM = HERE.parent
REPO = PROGRAM.parent
sys.path.insert(0, str(PROGRAM))
os.environ.setdefault("QT_QPA_PLATFORM", os.environ.get("SSQ_QPA", "minimal:enable_fonts"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FAILED: list[str] = []
_APP = None


def app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication
        import ssq_common as ui
        _APP = QApplication.instance() or QApplication(sys.argv)
        ui.apply_app_style(_APP)
        ui.init_ui_thread()
    return _APP


def pump(n: int = 5):
    a = app()
    for _ in range(n):
        a.processEvents()


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def finish():
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
        sys.exit(1)
    print("all passed")
