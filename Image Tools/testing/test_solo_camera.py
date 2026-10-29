"""Double-click a camera → it alone moves into a window of its own; the rest stay.

Since 26.10.2026 the double-click no longer puts the camera full-screen. It lifts that
one tile into a movable, resizable window (focus mode for one camera) and the other
cameras re-tile in the Slider.

Claims:
  1. A left double-click on a tile's picture (no drawing tool armed) lifts THAT tile
     into a borderless window; the other tiles stay in the grid.
  2. The tile's selection is the same before and after the double-click.
  3. A right-drag on the lifted-out picture zooms into that part.
  4. Esc puts it back into the grid, which is rebuilt with every tile.
  5. A double-click inside the lifted-out camera puts it back as well.
  6. Closing the window (Alt+F4) puts it back instead of losing the tile.
  7. A rebuilt camera set (new time window) keeps the camera lifted out, now showing
     its new tile, in the same window.
  8. With a drawing tool armed, a double-click stays a press on the picture.

Runs headless (offscreen — nothing on screen, no focus taken):
    python testing/test_solo_camera.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="it_solo_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt                       # noqa: E402
from PySide6.QtGui import QImage, QPixmap                   # noqa: E402
from PySide6.QtTest import QTest                            # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget  # noqa: E402

import is_t                                                 # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


class _StubViewer(QWidget):
    _is_multi_cam     = is_t.Viewer._is_multi_cam
    _solo_toggle      = is_t.Viewer._solo_toggle
    _solo_toggle_now  = is_t.Viewer._solo_toggle_now
    _pop_key          = is_t.Viewer._pop_key
    _pop_enter        = is_t.Viewer._pop_enter
    _pop_leave        = is_t.Viewer._pop_leave
    _pop_remember     = is_t.Viewer._pop_remember
    _pop_tile_gone    = is_t.Viewer._pop_tile_gone
    _pop_leave_all    = is_t.Viewer._pop_leave_all
    _pop_rehome       = is_t.Viewer._pop_rehome
    _rect_on_a_screen = staticmethod(is_t.Viewer._rect_on_a_screen)
    _POP_STATE_KEY    = is_t.Viewer._POP_STATE_KEY

    def __init__(self, names):
        super().__init__()
        self._cam_names = list(names)
        self._focus_mode = False
        self._watcher_mode = False
        self._pop_windows = {}
        self._ui_state = {}
        self.items = [1]
        row = QHBoxLayout(self)
        self._img_pointing_row = row
        self._single_wrapper = QWidget()
        self.img_view = is_t.ImageView(self._single_wrapper)
        self._single_wrapper.setVisible(False)
        row.addWidget(self._single_wrapper)
        self._multi_grid = is_t.MultiCameraGrid(self)
        row.addWidget(self._multi_grid, 1)
        self._multi_grid.camera_double_clicked.connect(self._solo_toggle)

    def _save_ui_state(self):
        pass

    def _schedule_hq(self):
        pass

    def _focus_raise_pv(self):
        pass


def _frame() -> QPixmap:
    img = QImage(400, 300, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.darkGray)
    return QPixmap.fromImage(img)


def _in_grid(grid, cv) -> bool:
    return grid.isAncestorOf(cv)


def _dclick(iv, pos=None):
    """One double-click, well apart from the previous one — back-to-back synthetic
    double-clicks inside the system interval read as a triple click."""
    QTest.qWait(QApplication.styleHints().mouseDoubleClickInterval() + 100)
    if pos is None:
        pos = QPoint(iv.width() // 2, iv.height() // 2)
    QTest.mouseDClick(iv, Qt.MouseButton.LeftButton, pos=pos)
    QTest.qWait(80)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    names = ["C01-A-CAM1NF", "C02-B-CAM2NF", "C03-C-CAM3NF"]
    v = _StubViewer(names)
    v._multi_grid.setup_cameras(names)
    v.resize(900, 400)
    v.show()                       # offscreen: nothing appears, no focus taken
    for cv in v._multi_grid._cam_views:
        cv.img_view.set_pixmap(_frame())
    QTest.qWait(50)

    grid = v._multi_grid
    cv = grid._cam_views[1]
    iv = cv.img_view
    key = cv.cam_name
    sel_before = grid.selected_cam_indices()
    centre = QPoint(iv.width() // 2, iv.height() // 2)

    print("Double-click lifts ONE camera out")
    QTest.mouseClick(iv, Qt.MouseButton.LeftButton, pos=centre)
    _dclick(iv, centre)
    win = v._pop_windows.get(key)
    check("a window exists for that camera", win is not None)
    check("it holds that tile", win is not None and cv.parentWidget() is win)
    check("borderless, not full screen", win is not None and not win.isFullScreen()
          and bool(win.windowFlags() & Qt.WindowType.FramelessWindowHint))
    check("the other tiles stay in the grid",
          all(_in_grid(grid, c) for c in grid._cam_views if c is not cv))
    check("the grid knows it is out", grid.is_popped(1) and not grid.is_popped(0))
    check("selection unchanged", grid.selected_cam_indices() == sel_before,
          f"{sel_before} → {grid.selected_cam_indices()}")

    print("Right-drag zooms into a part of the lifted-out camera")
    QTest.qWait(50)
    c2 = QPoint(iv.width() // 2, iv.height() // 2)
    a = c2 - QPoint(40, 30)
    b = c2 + QPoint(40, 30)
    QTest.mousePress(iv, Qt.MouseButton.RightButton, pos=a)
    QTest.mouseMove(iv, b)
    QTest.mouseRelease(iv, Qt.MouseButton.RightButton, pos=b)
    QTest.qWait(30)
    check("zoomed", getattr(iv, "_zoom_norm", None) is not None)
    iv.reset_zoom()

    print("Esc puts it back")
    QTest.keyClick(win, Qt.Key.Key_Escape)
    QTest.qWait(50)
    check("window gone", key not in v._pop_windows)
    check("tile back in the grid", _in_grid(grid, cv))
    check("grid has every tile", all(_in_grid(grid, c) for c in grid._cam_views))
    check("nothing marked out", not grid._popped)

    print("Double-click inside the lifted-out camera puts it back")
    _dclick(iv, centre)
    win = v._pop_windows.get(key)
    check("lifted out again", win is not None)
    _dclick(iv)
    check("and back", key not in v._pop_windows and _in_grid(grid, cv))

    print("Closing the window puts the camera back")
    _dclick(iv, centre)
    win = v._pop_windows.get(key)
    if win is not None:
        win.close()
    QTest.qWait(50)
    check("closed → back in the grid", key not in v._pop_windows and _in_grid(grid, cv))

    print("A new camera set keeps the camera out, in the same window")
    _dclick(iv, centre)
    win = v._pop_windows.get(key)
    geo = win.geometry() if win is not None else None
    grid.setup_cameras(names)          # what every scan does
    v._pop_rehome()                    # what _setup_multi_cam does right after
    for c in grid._cam_views:          # the new tiles get their frames
        c.img_view.set_pixmap(_frame())
    QTest.qWait(50)
    new_cv = next(c for c in grid._cam_views if c.cam_name == key)
    win2 = v._pop_windows.get(key)
    check("same window", win2 is not None and win2 is win)
    check("same place", win2 is not None and win2.geometry() == geo)
    check("shows the NEW tile", win2 is not None and new_cv.parentWidget() is win2)
    check("the rest are in the grid",
          all(_in_grid(grid, c) for c in grid._cam_views if c is not new_cv))
    v._pop_leave_all()
    QTest.qWait(50)
    check("all back", not v._pop_windows
          and all(_in_grid(grid, c) for c in grid._cam_views))

    print("A drawing tool keeps the double-click for itself")
    cv = grid._cam_views[1]
    iv = cv.img_view
    iv.set_draw_mode("cross")
    _dclick(iv, centre)
    check("no window", not v._pop_windows)
    iv.set_draw_mode("")

    print()
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
