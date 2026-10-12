"""Middle-click a camera to undo its zoom — asked for 24.09.2026.

Right-drag zooms into a picture; the only way back was the Reset zoom button in the
settings column, which asks about the SELECTED cameras and puts up a question when
none are selected. A middle click on the picture itself resets that one camera and
nothing else.

What is checked, driving the widgets in process (no synthetic desktop input):

  * a middle click on a zoomed camera puts it back to the whole frame;
  * it resets ONLY the camera clicked — the other tiles keep their zoom;
  * it works on an unselected tile, and does NOT count as a click on the camera,
    so middle-clicking one tile cannot change which cameras the settings act on;
  * a middle click on a camera that is not zoomed does nothing at all (no redundant
    zoom_changed, so no reload of the frame);
  * and left and right clicks are untouched — a right press still starts the
    rubber band.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/test_middle_click_reset_zoom.py

No window is ever shown.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_midclick_"))
os.environ["APPDATA"] = str(_TMP)

from PySide6.QtCore import QPointF, Qt                                # noqa: E402
from PySide6.QtGui import QMouseEvent, QPixmap                        # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

import is_t                                                           # noqa: E402

FAILURES: "list[str]" = []

ZOOM = (0.25, 0.25, 0.75, 0.75)


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def _ev(widget, kind, button, buttons, x, y):
    """One mouse event built the way Qt6 wants it — local AND global position, so no
    deprecated constructor is used. The tile code reads globalPosition()."""
    local = QPointF(x, y)
    glob = QPointF(widget.mapToGlobal(local.toPoint()))
    return QMouseEvent(kind, local, glob, button, buttons,
                       Qt.KeyboardModifier.NoModifier)


def press(widget, button, x=60, y=50):
    """One mouse press delivered straight to the widget — no OS input involved."""
    ev = _ev(widget, QMouseEvent.Type.MouseButtonPress, button, button, x, y)
    widget.mousePressEvent(ev)
    return ev


def release(widget, button, x=60, y=50):
    ev = _ev(widget, QMouseEvent.Type.MouseButtonRelease, button,
             Qt.MouseButton.NoButton, x, y)
    widget.mouseReleaseEvent(ev)
    return ev


def make_tile(app, idx: int) -> "is_t.CameraView":
    cv = is_t.CameraView(idx, f"C03-08{idx}-MIDCLICK-_-IMG")
    cv.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    cv.resize(220, 200)
    cv.show()
    pm = QPixmap(160, 120)
    pm.fill(Qt.GlobalColor.darkGray)
    cv.img_view.set_pixmap(pm)
    app.processEvents()
    return cv


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    tiles = [make_tile(app, i) for i in range(3)]
    clicks: "list[int]" = []
    for cv in tiles:
        cv.clicked.connect(clicks.append)
        cv.img_view.set_zoom(ZOOM)
    app.processEvents()
    check("all three cameras start zoomed in",
          all(cv.img_view._zoom_norm == ZOOM for cv in tiles),
          str([cv.img_view._zoom_norm for cv in tiles]))

    # Middle click on the MIDDLE tile, which is not selected.
    zoom_signals = []
    tiles[1].img_view.zoom_changed.connect(lambda: zoom_signals.append(1))
    press(tiles[1].img_view, Qt.MouseButton.MiddleButton)
    release(tiles[1].img_view, Qt.MouseButton.MiddleButton)
    app.processEvents()

    check("the camera clicked shows the whole frame again",
          tiles[1].img_view._zoom_norm is None,
          str(tiles[1].img_view._zoom_norm))
    check("the other cameras keep their zoom",
          tiles[0].img_view._zoom_norm == ZOOM
          and tiles[2].img_view._zoom_norm == ZOOM,
          str([tiles[0].img_view._zoom_norm, tiles[2].img_view._zoom_norm]))
    check("the frame is re-rendered once for the camera reset",
          len(zoom_signals) == 1, f"{len(zoom_signals)} zoom_changed")
    check("it did not count as a click on the camera", clicks == [],
          f"selected {clicks}")

    # Again on the same tile: nothing to undo, so nothing may happen — a second
    # zoom_changed would reload the frame off the share for no reason.
    press(tiles[1].img_view, Qt.MouseButton.MiddleButton)
    release(tiles[1].img_view, Qt.MouseButton.MiddleButton)
    app.processEvents()
    check("a middle click with no zoom to undo does nothing",
          tiles[1].img_view._zoom_norm is None and len(zoom_signals) == 1
          and clicks == [],
          f"{len(zoom_signals)} zoom_changed, selected {clicks}")

    # The other buttons are untouched: a right press arms the rubber band, and a
    # left press on a camera with no drawing mode does not reset anything.
    iv = tiles[0].img_view
    press(iv, Qt.MouseButton.RightButton)
    check("a right press still starts the rubber band",
          iv._rb_start is not None, str(iv._rb_start))
    iv._rb_start = None
    iv._rb_current = None
    press(iv, Qt.MouseButton.LeftButton)
    app.processEvents()
    check("a left press leaves the zoom alone", iv._zoom_norm == ZOOM,
          str(iv._zoom_norm))

    # One camera on its own: the same picture widget, so the same gesture.
    single = is_t.ImageView()
    single.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    single.resize(320, 240)
    single.show()
    pm = QPixmap(160, 120)
    pm.fill(Qt.GlobalColor.darkGray)
    single.set_pixmap(pm)
    single.set_zoom(ZOOM)
    app.processEvents()
    press(single, Qt.MouseButton.MiddleButton)
    release(single, Qt.MouseButton.MiddleButton)
    app.processEvents()
    check("it works with one camera on screen too",
          single._zoom_norm is None, str(single._zoom_norm))

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
