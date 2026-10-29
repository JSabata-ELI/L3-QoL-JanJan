"""SPFE Values -- the wheel guard and the taskbar-icon nudge.

    python testing/test_wheel_guard.py

The wheel rule (15.10.2026): a number box, drop-down or slider answers the
wheel only after it was clicked (selected), and only while the pointer is
over it. Moving the pointer away does not deselect it; selecting something
else does. Pinned here:

  * installing the guard twice leaves one filter on the application;
  * a polished spin box / drop-down has StrongFocus, not WheelFocus (with
    WheelFocus Qt focuses the field on a wheel notch before any filter sees it);
  * a press on the spin box's own text line selects it (wheelReady);
  * the pointer leaving keeps it selected;
  * focus going to another control (a mouse focus-out) ends it;
  * focus leaving because another window was activated keeps it.

And that the main module has `_nudge_taskbar_icon` and calls it.

Headless and silent: offscreen platform, nothing is shown on a screen, no
window is activated, no OS input is injected -- events are handed to Qt
directly.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QEvent, QPointF, Qt                      # noqa: E402
from PySide6.QtGui import QFocusEvent, QMouseEvent                  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QLineEdit,  # noqa: E402
                               QScrollArea, QSpinBox, QVBoxLayout,
                               QWidget)

_app = QApplication.instance() or QApplication([])

import main as _guard_mod                               # noqa: E402

MAIN_FILE = HERE.parent / "main.py"


def _build():
    """A spin box and a drop-down inside a scroll area, polished, never on
    a screen."""
    area = QScrollArea()
    area.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    area.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    spin = QSpinBox()
    spin.setRange(0, 100)
    combo = QComboBox()
    combo.addItems(["a", "b", "c"])
    lay.addWidget(spin)
    lay.addWidget(combo)
    area.setWidget(inner)
    area.setWidgetResizable(True)
    area.ensurePolished()
    spin.ensurePolished()
    combo.ensurePolished()
    area.show()               # WA_DontShowOnScreen + offscreen: never visible
    _app.processEvents()
    return area, spin, combo


def test_installed_once():
    _guard_mod.install_wheel_guard(_app)
    first = getattr(_app, "_wheel_guard", None)
    _guard_mod.install_wheel_guard(_app)
    _guard_mod.install_wheel_guard()
    assert first is not None, "the guard was not kept on the application"
    assert getattr(_app, "_wheel_guard", None) is first, \
        "a second install put another filter on the application"


def test_polish_takes_wheel_focus_away():
    _guard_mod.install_wheel_guard(_app)
    area, spin, combo = _build()
    try:
        assert spin.focusPolicy() == Qt.FocusPolicy.StrongFocus, \
            f"spin box focus policy {spin.focusPolicy()}"
        assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus, \
            f"drop-down focus policy {combo.focusPolicy()}"
    finally:
        area.hide()
        area.deleteLater()


def test_click_selects_leave_keeps_focus_out_ends():
    _guard_mod.install_wheel_guard(_app)
    area, spin, combo = _build()
    try:
        assert not spin.property("wheelReady"), "selected before any click"
        line = spin.findChild(QLineEdit)
        assert line is not None, "the spin box has no text line"
        press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(3, 3),
                            QPointF(3, 3), Qt.MouseButton.LeftButton,
                            Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(line, press)
        assert spin.property("wheelReady") is True, \
            "a click on the text line did not select the spin box"

        QApplication.sendEvent(spin, QEvent(QEvent.Type.Leave))
        assert spin.property("wheelReady") is True, \
            "the pointer leaving deselected the spin box"

        QApplication.sendEvent(spin, QFocusEvent(
            QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))
        assert spin.property("wheelReady") is False, \
            "clicking another control did not deselect the spin box"

        # Selected again, then another window takes the focus for a moment.
        QApplication.sendEvent(line, QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(3, 3), QPointF(3, 3),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier))
        assert spin.property("wheelReady") is True, "re-selecting failed"
        QApplication.sendEvent(spin, QFocusEvent(
            QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        assert spin.property("wheelReady") is True, \
            "another window being activated deselected the spin box"
    finally:
        area.hide()
        area.deleteLater()


def test_main_nudges_taskbar_icon():
    tree = ast.parse(MAIN_FILE.read_text(encoding="utf-8"))
    defined = any(isinstance(n, ast.FunctionDef)
                  and n.name == "_nudge_taskbar_icon" for n in tree.body)
    assert defined, f"{MAIN_FILE.name} has no _nudge_taskbar_icon"
    called = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "_nudge_taskbar_icon"
                 for n in ast.walk(tree))
    assert called, f"{MAIN_FILE.name} never calls _nudge_taskbar_icon"


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL  {name}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)
