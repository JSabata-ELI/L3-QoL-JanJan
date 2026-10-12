"""
Graph tab focus mode (F11): the window can be moved and resized.

  * F11 → windowed, with an 8 px frame round the graph; Ctrl+F11 → fullscreen,
    no frame;
  * a left press anywhere on the graph starts a window move — also on a widget
    added AFTER the window opened (the graph canvas is rebuilt on every replot);
  * a press on the frame starts a resize from that edge / corner, and the
    cursor over the frame shows the resize arrows;
  * fullscreen neither moves nor resizes;
  * after docking back, presses on the graph are left alone again.

Runs headless, no archiver needed. Window moves are recorded, not performed.

Run:  python test_graph_focus_move_resize.py   (prints PASS/FAIL, exits non-zero)
"""
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import main as app_main                                   # noqa: E402
from PySide6.QtCore import Qt, QEvent, QPointF            # noqa: E402
from PySide6.QtGui import QMouseEvent                     # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget       # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
app.setStyleSheet(app_main._APP_STYLESHEET)
app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
w = app_main.CSSLoggerWidget()
w._request_reload = lambda *a, **k: None
w._save_runtime_state = lambda *a, **k: None
w.move(-4000, -4000)
w.show()
app.processEvents()

failures = []


def check(what, ok):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


class FakeHandle:
    def __init__(self):
        self.moves, self.resizes = 0, []

    def startSystemMove(self):
        self.moves += 1
        return True

    def startSystemResize(self, edges):
        self.resizes.append(edges)
        return True


def press(widget, x, y, button=Qt.MouseButton.LeftButton):
    p = QPointF(x, y)
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, p, widget.mapToGlobal(p),
                     button, button, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


def hover(widget, x, y):
    p = QPointF(x, y)
    ev = QMouseEvent(QEvent.Type.MouseMove, p, widget.mapToGlobal(p),
                     Qt.MouseButton.NoButton, Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


# ── windowed ───────────────────────────────────────────────────────────────
w._graph_popout(windowed=True)
pop = w._graph_popup
pop.move(-3000, -3000)
app.processEvents()
fake = FakeHandle()
pop.windowHandle = lambda: fake
m = pop.layout().contentsMargins()
check("windowed: 8 px frame round the graph", (m.left(), m.top(), m.right(), m.bottom()) == (8, 8, 8, 8))

inner = w._tab_graph
press(inner, 50, 50)
check("left press on the graph moves the window", fake.moves == 1)

late = QWidget(inner)            # stands in for a canvas rebuilt by a replot
late.setGeometry(10, 10, 50, 50)
late.show()
press(late, 5, 5)
check("left press on a widget added later also moves", fake.moves == 2)

press(inner, 50, 50, Qt.MouseButton.RightButton)
check("right press does not move (zoom stays)", fake.moves == 2)

press(pop, pop.width() - 3, pop.height() // 2)
check("press on the right frame resizes from the right",
      fake.resizes[-1:] == [Qt.Edge.RightEdge])
press(pop, 3, 3)
check("press on the top-left corner resizes both ways",
      fake.resizes[-1:] == [Qt.Edge.LeftEdge | Qt.Edge.TopEdge])
hover(pop, pop.width() - 3, pop.height() - 3)
check("cursor over the corner shows the resize arrows",
      pop.cursor().shape() == Qt.CursorShape.SizeFDiagCursor)

# ── fullscreen ────────────────────────────────────────────────────────────
w._graph_popout(windowed=False)
app.processEvents()
m = pop.layout().contentsMargins()
check("fullscreen: no frame", (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0))
if pop.isFullScreen():
    n_moves, n_res = fake.moves, len(fake.resizes)
    press(inner, 50, 50)
    press(pop, 1, 1)
    check("fullscreen neither moves nor resizes",
          fake.moves == n_moves and len(fake.resizes) == n_res)
else:
    print("skip  fullscreen move check (platform kept the window normal)")

# ── docked back ───────────────────────────────────────────────────────────
w._restore_graph_from_popup()
app.processEvents()
n_moves = fake.moves
press(inner, 50, 50)
check("after docking back, a press on the graph is left alone", fake.moves == n_moves)
check("graph tab is back in the notebook", w._notebook.indexOf(w._tab_graph) >= 0)

print("PASS" if not failures else f"FAIL ({len(failures)})")
sys.exit(1 if failures else 0)
