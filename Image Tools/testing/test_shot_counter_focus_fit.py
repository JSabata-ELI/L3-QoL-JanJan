"""Shot counter, focus windows that fit the picture, marks with nothing selected.

What is pinned here:

  1. (The shot counter is pinned in test_shot_counter_sources.py.)
  2. A focus-mode camera window is cut down to its picture: no grey band left, and
     the picture itself not one pixel smaller.
  3. With no camera selected, a mark drawn on one camera stays on that camera. With
     cameras selected it is still copied to all of them.

Runs headless, no share and no archiver:

    python testing/test_shot_counter_focus_fit.py
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

import is_t

S = 1_000_000_000


def _pixmap(w: int, h: int) -> QPixmap:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(0xFF404040)
    return QPixmap.fromImage(img)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # ── 1. shot counter: moved to test_shot_counter_sources.py ────────────────

    # ── 2. focus window fits the picture ──────────────────────────────────────
    print("Focus window fits the picture")
    viewer = SimpleNamespace(_focus_raise_pv=lambda: None, _focus_mode=True,
                             _focus_toggling=False, _toggle_focus_mode=lambda: None)
    for (pw, ph), (ww, wh) in (((1600, 900), (616, 616)),     # wide frame, square win
                               ((600, 1200), (816, 516))):    # tall frame, wide win
        cv = is_t.CameraView(0, "CAM_TEST")
        cv.img_view.set_pixmap(_pixmap(pw, ph))
        fw = is_t._FocusWindow(cv, viewer, "CAM_TEST")
        fw.show()
        fw.setGeometry(0, 0, ww, wh)
        for _ in range(5):
            app.processEvents()
        before = cv.img_view._img_rect()
        fw.fit_to_picture()
        for _ in range(10):
            app.processEvents()
        after = cv.img_view._img_rect()
        iv = cv.img_view
        spare_w = iv.width() - after.width()
        spare_h = iv.height() - iv._label_bar_h() - after.height()
        tag = f"{pw}x{ph} in {ww}x{wh}"
        check(after.width() >= before.width() and after.height() >= before.height(),
              f"{tag}: picture keeps its size "
              f"({before.width()}x{before.height()} → {after.width()}x{after.height()})")
        check(spare_w <= 1 and spare_h <= 1,
              f"{tag}: no grey band left ({spare_w} px wide, {spare_h} px tall)")
        fw.release_content(None)
        fw.hide()
        fw.deleteLater()
        cv.deleteLater()

    # ── 3. marks with nothing selected ────────────────────────────────────────
    print("Marks with nothing selected stay on the clicked camera")
    views = [is_t.ImageView() for _ in range(3)]
    for v in views:
        v.resize(300, 200)
        v.set_pixmap(_pixmap(300, 200))
    sel = []
    grid = SimpleNamespace(_cam_views=views,
                           selected_cam_indices=lambda: list(sel),
                           get_img_view=lambda i: views[i] if 0 <= i < len(views) else None)
    host = SimpleNamespace(_multi_grid=grid, _is_multi_cam=lambda: True)
    host._overlay_target_indices = lambda: is_t.Viewer._overlay_target_indices(host)
    host._copy_overlay_shape = is_t.Viewer._copy_overlay_shape

    views[1].show_cross = True
    views[1].add_mark("cross", (0.25, 0.75))
    is_t.Viewer._on_overlay_edited(host, 1, "cross")
    check(views[1].marks.get("cross") == [(0.25, 0.75)], "the clicked camera has its mark")
    check(not views[0].marks.get("cross") and not views[2].marks.get("cross"),
          "no other camera got it")

    sel[:] = [0, 2]
    views[0].show_cross = True
    views[0].add_mark("cross", (0.5, 0.5))
    is_t.Viewer._on_overlay_edited(host, 0, "cross")
    check(views[2].marks.get("cross") == [(0.5, 0.5)],
          "with cameras selected the mark is copied to the others selected")
    check(views[1].marks.get("cross") == [(0.25, 0.75)],
          "and an unselected camera keeps its own")

    print()
    if fails:
        print(f"{len(fails)} FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
