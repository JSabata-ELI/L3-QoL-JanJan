"""Can the alarm window and the circle actually be dragged?

Run:  python testing/test_placing.py

The bug this is about, reported on 2026-09-18: "Place the alarm window" and
"Place the circle" both showed the thing, and neither of them could be moved by
so much as a pixel.

The cause was not in the dragging code, which asks Windows to do it and is the
right way round. It was the little instruction box beside it. Both were opened
with `QDialog.exec()`, and `exec()` sets `WA_ShowModal` itself — whatever
`setModal(False)` said when the box was built. An application-modal dialog
blocks mouse events to every OTHER top-level window in the program, and the
alarm window and the circle ARE other top-level windows. The press that would
have started the drag was never delivered to them.

So the test that matters is: while one of these boxes is up, is anything in this
program application-modal? Nothing is clicked or typed anywhere — the handlers
are called and the widgets' own signals are emitted, which are ordinary
function calls.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QPoint, Qt                       # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_alarm as AL                                      # noqa: E402


def _alarm(cfg=None):
    return AL.AlarmWindow(cfg if cfg is not None else {})


# ── the edges, which decide move-versus-size ────────────────────────────────

def test_the_middle_moves_and_the_edges_size():
    w = _alarm()
    w.resize(200, 120)
    d = AL._PLACE_EDGE
    cases = {
        (100, 60): Qt.Edge(0),                                  # the middle
        (1, 60): Qt.Edge.LeftEdge,
        (199, 60): Qt.Edge.RightEdge,
        (100, 0): Qt.Edge.TopEdge,
        (100, 119): Qt.Edge.BottomEdge,
        (0, 0): Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
        (199, 0): Qt.Edge.RightEdge | Qt.Edge.TopEdge,
        (0, 119): Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
        (199, 119): Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
    }
    for (x, y), want in cases.items():
        got = w._edges_at(QPoint(x, y))
        assert got == want, f"at {x},{y} expected {want!r}, got {got!r}"
    # Just inside the margin is still the edge; one pixel further in is not.
    assert w._edges_at(QPoint(d - 1, 60)) == Qt.Edge.LeftEdge
    assert w._edges_at(QPoint(d, 60)) == Qt.Edge(0)
    w.deleteLater()


def test_every_edge_has_a_pointer_of_its_own():
    """A grip that does not say it is a grip is not a grip."""
    w = _alarm()
    shapes = {edges: AL.AlarmWindow._EDGE_CURSORS[edges]
              for edges in AL.AlarmWindow._EDGE_CURSORS}
    assert len(shapes) == 8, "four sides and four corners"
    assert Qt.CursorShape.ArrowCursor not in shapes.values()
    w.deleteLater()


# ── the box beside it must not be modal ─────────────────────────────────────

def test_the_alarm_box_leaves_the_rest_of_the_program_alone():
    alarm = _alarm()
    dlg = AL.PlaceAlarmDialog(None, alarm)
    dlg.show()
    _app.processEvents()
    assert not dlg.isModal(), "exec() was used, or setModal(True) crept back"
    assert QApplication.activeModalWidget() is None, \
        "something is application-modal — the alarm window cannot be dragged"
    dlg.close()
    dlg.deleteLater()
    alarm.deleteLater()


def test_the_circle_box_leaves_the_rest_of_the_program_alone():
    dlg = AL.PlaceCircleDialog(None)
    dlg.show()
    _app.processEvents()
    assert not dlg.isModal()
    assert QApplication.activeModalWidget() is None
    dlg.close()
    dlg.deleteLater()


def test_neither_box_is_opened_with_exec():
    """Read as source, because `exec()` is the mistake and not its result.

    A box built non-modally and then opened with `exec()` is modal anyway, and
    the two tests above cannot see that: they show the box themselves. So the
    Alarm tab's own two handlers are read, and `exec` must not appear in them.
    """
    import ast
    src = (HERE.parent / "al_t.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = {"_place", "_place_circle"}
    seen = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in wanted:
            continue
        seen.add(node.name)
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Attribute) and inner.attr == "exec"):
                raise AssertionError(
                    f"{node.name} opens its box with exec() — the alarm window "
                    f"and the circle cannot be dragged while it is up")
    assert seen == wanted, f"handlers not found: {sorted(wanted - seen)}"


# ── placing must not be mistaken for using ──────────────────────────────────

def test_a_click_on_the_circle_being_placed_is_not_an_order():
    """`clicked` stops watching and brings the window back."""
    hud = AL.HudWindow({})
    heard = []
    hud.clicked.connect(lambda: heard.append(1))

    hud.start_placing()
    hud._press = QPoint(10, 10)          # as a press would have left it
    hud.mouseReleaseEvent(_Release())
    assert not heard, "letting go of the circle you are dragging stopped watching"

    hud.stop_placing(keep=False)
    hud._press = QPoint(10, 10)
    hud.mouseReleaseEvent(_Release())
    assert heard == [1], "a normal click must still be an order"
    hud.deleteLater()


class _Release:
    """The one thing `mouseReleaseEvent` asks of its event."""

    @staticmethod
    def button():
        return Qt.MouseButton.LeftButton


def test_the_window_cannot_be_sized_down_to_nothing():
    w = _alarm()
    w.start_placing()
    smallest = w.minimumSize()
    assert smallest.width() >= 2 * AL._PLACE_EDGE, \
        "sized to nothing it has no edge left to grab"
    w.stop_placing(keep=False)
    assert w.minimumSize().width() == 0, "the alarm itself is any size it likes"
    w.deleteLater()


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            bad += 1
            print(f"  ERROR {name}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
