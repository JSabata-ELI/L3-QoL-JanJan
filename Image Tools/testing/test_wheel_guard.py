"""The wheel guard and the taskbar-icon nudge, checked offscreen.

Rule (15.10.2026): a number box / drop-down / slider answers the wheel only
after it was clicked (selected) and only while the pointer is over it; the
pointer leaving does not deselect it, selecting something else does.

  * install_wheel_guard installs ONE filter however often it is called;
  * spin boxes and drop-downs lose WheelFocus when polished;
  * a press on a spin box's text line selects the box; Leave keeps it;
    FocusOut to another control ends it; FocusOut to another window or the
    drop-down's own list keeps it;
  * an unselected field passes the wheel on, "wheelAlways" always takes it,
    scroll bars are never touched, a broken event never raises;
  * the copies in is_t.py, if_t.py and sf_t.py are the same text as main.py's;
  * main.py defines _nudge_taskbar_icon right after _icon_app_id and calls it.

Nothing is shown and nothing takes focus.  Run:  python testing/test_wheel_guard.py
"""
import ast
import importlib.util
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent.parent

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QFocusEvent, QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QScrollArea,  # noqa: E402
                               QScrollBar, QSlider, QSpinBox, QVBoxLayout,
                               QWidget)

APP = QApplication.instance() or QApplication([])

spec = importlib.util.spec_from_file_location("image_tools_main", str(HERE / "main.py"))
it_main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(it_main)

FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what + ("" if ok else f"   [{detail}]"))
    if not ok:
        FAILS.append(what)


def wheel_event():
    return QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


def press(w):
    QApplication.sendEvent(w, QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier))


def focus_out(w, reason):
    QApplication.sendEvent(w, QFocusEvent(QEvent.Type.FocusOut, reason))


# ── installed once ──────────────────────────────────────────────────────────
print("the wheel guard")
installed = []
_orig_install = APP.installEventFilter
APP.installEventFilter = lambda f: (installed.append(f), _orig_install(f))[-1]
it_main.install_wheel_guard(APP)
it_main.install_wheel_guard(APP)
del APP.installEventFilter
check("installed twice -> one filter", len(installed) == 1, str(installed))
guard = getattr(APP, "_wheel_guard", None)
check("the guard is kept on the application", guard is not None)

# ── polish ──────────────────────────────────────────────────────────────────
host = QScrollArea()
inner = QWidget()
lay = QVBoxLayout(inner)
spin = QSpinBox()
spin.setRange(0, 100)
spin.setValue(50)
combo = QComboBox()
combo.addItems(["a", "b", "c"])
slider = QSlider(Qt.Orientation.Horizontal)
lay.addWidget(spin)
lay.addWidget(combo)
lay.addWidget(slider)
host.setWidget(inner)
for w in (host, inner, spin, combo, slider):
    w.ensurePolished()
check("a polished spin box is StrongFocus",
      spin.focusPolicy() == Qt.FocusPolicy.StrongFocus, str(spin.focusPolicy()))
check("a polished drop-down is StrongFocus",
      combo.focusPolicy() == Qt.FocusPolicy.StrongFocus, str(combo.focusPolicy()))

# ── select / keep / end ─────────────────────────────────────────────────────
check("untouched spin box is not selected", not spin.property("wheelReady"))
press(spin.lineEdit())
check("a press on the text line selects the spin box", spin.property("wheelReady") is True)
QApplication.sendEvent(spin, QEvent(QEvent.Type.Leave))
check("the pointer leaving keeps it selected", spin.property("wheelReady") is True)
focus_out(spin, Qt.FocusReason.MouseFocusReason)
check("another control clicked -> no longer selected", spin.property("wheelReady") is False)
press(spin.lineEdit())
focus_out(spin, Qt.FocusReason.ActiveWindowFocusReason)
check("another window activated keeps it selected", spin.property("wheelReady") is True)
focus_out(spin, Qt.FocusReason.PopupFocusReason)
check("a popup opening keeps it selected", spin.property("wheelReady") is True)

# ── the wheel itself ────────────────────────────────────────────────────────
spin.hasFocus = lambda: True        # offscreen never gives real focus
check("selected + focused: the wheel reaches the spin box",
      guard.eventFilter(spin, wheel_event()) is False)
focus_out(spin, Qt.FocusReason.TabFocusReason)
check("after Tab away the wheel is passed to the panel",
      guard.eventFilter(spin, wheel_event()) is True)
v0 = spin.value()
QApplication.sendEvent(spin, wheel_event())
check("an unselected spin box keeps its value", spin.value() == v0, f"{v0} -> {spin.value()}")
check("an untouched drop-down does not take the wheel",
      guard.eventFilter(combo, wheel_event()) is True)
slider.setProperty("wheelAlways", True)
check("a 'wheelAlways' slider always takes the wheel",
      guard.eventFilter(slider, wheel_event()) is False)
check("a scroll bar is never touched",
      guard.eventFilter(QScrollBar(), wheel_event()) is False)
check("a broken event never raises", guard.eventFilter(spin, None) is False)

# ── the copies ──────────────────────────────────────────────────────────────
print("\nthe copies")


def guard_source(path):
    text = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "install_wheel_guard":
            return ast.get_source_segment(text, node)
    return None


ref = guard_source(HERE / "main.py")
check("main.py has install_wheel_guard", ref is not None)
for name in ("is_t.py", "if_t.py", "sf_t.py"):
    src = guard_source(HERE / name)
    check(f"{name}: install_wheel_guard is the same text as main.py's",
          src is not None and src == ref)

# ── taskbar icon nudge ──────────────────────────────────────────────────────
print("\nthe taskbar icon")
text = (HERE / "main.py").read_text(encoding="utf-8-sig")
tree = ast.parse(text)
names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
check("main.py defines _nudge_taskbar_icon", "_nudge_taskbar_icon" in names)
check("it sits right after _icon_app_id",
      "_icon_app_id" in names and "_nudge_taskbar_icon" in names
      and names.index("_nudge_taskbar_icon") == names.index("_icon_app_id") + 1,
      str(names))
calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
         and isinstance(n.func, ast.Name) and n.func.id == "_nudge_taskbar_icon"]
check("main.py calls it", len(calls) >= 1)
check("it is called with the shown window's handle",
      any("winId" in ast.get_source_segment(text, c) for c in calls))
it_main._nudge_taskbar_icon(0)      # no window: returns at once, starts nothing
check("a zero handle does nothing", True)

print()
print("ALL PASSED" if not FAILS else f"{len(FAILS)} FAILED: " + ", ".join(FAILS))
sys.exit(1 if FAILS else 0)
