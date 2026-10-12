"""Test: the PV TABLE section names the loaded style, and the window title
carries the running version.

Rendered with the real Windows backend (offscreen has no fonts), shown WITHOUT
taking the keyboard focus. Saves testing/_out/sidebar_style_current.png.

    python testing/test_style_current_and_version.py
"""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt                         # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import main as css                                    # noqa: E402
import test_smoke as smoke                            # noqa: E402

css.save_config = lambda *a, **k: None                # never touch the real settings
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
FAILS = []


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        FAILS.append(name)


def pump(n=6):
    for _ in range(n):
        QApplication.processEvents()


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    css.install_app_look(app)

    # ── version from the exe name ───────────────────────────────────────────
    old_exe, had_frozen = sys.executable, hasattr(sys, "frozen")
    try:
        check("source run has no version", css._app_version() == "")
        sys.frozen = True
        sys.executable = r"C:\x\CSS Logger\v1.4.2\CSS Logger v1.4.2.exe"
        check("frozen exe gives v1.4.2", css._app_version() == "v1.4.2")
        sys.executable = r"C:\x\CSS Logger.exe"
        check("exe without version gives nothing", css._app_version() == "")
    finally:
        sys.executable = old_exe
        if not had_frozen:
            del sys.frozen

    # ── loaded style label ──────────────────────────────────────────────────
    pvs = ("L3-TEST-A:Energy", "L3-TEST-B:Energy")
    w = smoke._loaded_widget(pvs)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    w.resize(1500, 950)
    w.move(40, 40)
    w._set_style_current("")
    w.show()
    pump()
    lbl = w._lbl_style_current
    check("nothing loaded says none", lbl.text() == "Loaded style: none")

    name = "Operation <night> look with a rather long name"
    w._style_presets[name] = w._current_style_payload()
    w._load_style_preset(name)
    pump()
    check("loaded name shown", "rather long name" in lbl.text())
    check("name is escaped", "&lt;night&gt;" in lbl.text())
    check("not kept for next start", "style_current" not in w.config)
    side = w._sidebar_widget.parentWidget().parentWidget()
    side.ensureWidgetVisible(lbl, 0, 120)
    pump()
    side.grab().save(os.path.join(OUT, "sidebar_style_current.png"))

    real_q = css.QMessageBox.question
    css.QMessageBox.question = staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        w._style_presets["other"] = {}
        w._delete_style_preset("other")
        check("deleting another keeps the name", w._style_current == name)
        w._delete_style_preset(name)
        check("deleting the loaded one clears it", lbl.text() == "Loaded style: none")
    finally:
        css.QMessageBox.question = real_q

    w.hide()
    print("FAILED:" if FAILS else "ALL PASS", *FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
