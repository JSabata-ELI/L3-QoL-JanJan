"""Overlays and calibration across several selected cameras (Image Slider).

Three claims, all about having more than one camera selected:

  1. Arming a drawing mode arms it on EVERY selected camera, not just the tile that
     was clicked last, and clears it on the ones that are not selected.
  2. Placing a mark puts it at the SAME normalized spot on every selected camera —
     the middle of one tile is the middle of all of them, whatever each camera's
     resolution is — and leaves the unselected cameras alone. Only the shape that
     moved is copied: dragging the cross must not disturb an existing circle.
  3. One click on a Cal button calibrates every selected camera on ITS OWN frame,
     so two cameras with the beam in different places get two different results.

Runs headless, no share and no archiver:

    python testing/test_multicam_overlays.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

import is_t


class _FakeBox:
    """Stand-in for QMessageBox so a failed calibration cannot open a modal dialog."""
    calls: list = []
    StandardButton = getattr(is_t.QMessageBox, "StandardButton", None)

    @classmethod
    def information(cls, *a, **k):
        cls.calls.append(("information", a[1] if len(a) > 1 else ""))

    @classmethod
    def warning(cls, *a, **k):
        cls.calls.append(("warning", a[1] if len(a) > 1 else ""))

    @classmethod
    def question(cls, *a, **k):
        cls.calls.append(("question", a[1] if len(a) > 1 else ""))
        return None


class _StubViewer:
    """Just enough of Viewer to run its real overlay methods against a real grid."""

    # the real methods under test
    _is_multi_cam           = is_t.Viewer._is_multi_cam
    _overlay_target_indices = is_t.Viewer._overlay_target_indices
    _overlay_targets        = is_t.Viewer._overlay_targets
    _copy_overlay_shape     = staticmethod(is_t.Viewer._copy_overlay_shape)
    _on_overlay_edited      = is_t.Viewer._on_overlay_edited
    _toggle_draw_mode       = is_t.Viewer._toggle_draw_mode
    _draw_mode_of_targets   = is_t.Viewer._draw_mode_of_targets
    _refresh_draw_btns      = is_t.Viewer._refresh_draw_btns
    _on_overlay_changed     = is_t.Viewer._on_overlay_changed
    _cam_name_of_view       = is_t.Viewer._cam_name_of_view
    _calibrate_shape        = is_t.Viewer._calibrate_shape
    calibrate_cross         = is_t.Viewer.calibrate_cross

    def __init__(self, grid, cam_names):
        self._multi_grid = grid
        self._cam_names = list(cam_names)
        self.img_view = is_t.ImageView()
        self.cb_cross = QCheckBox("Cross")
        self.cb_circle = QCheckBox("Circle")
        self.cb_square = QCheckBox("Square")
        self.btn_draw_cross = QPushButton()
        self.btn_draw_circle = QPushButton()
        self.btn_draw_square = QPushButton()
        # Wired exactly as the panel wires them: each box carries its own shape.
        self.cb_cross.stateChanged.connect(lambda _s: self._on_overlay_changed("cross"))
        self.cb_circle.stateChanged.connect(lambda _s: self._on_overlay_changed("circle"))
        self.cb_square.stateChanged.connect(lambda _s: self._on_overlay_changed("square"))


def _blob_pixmap(w: int, h: int, cx: float, cy: float, r: int = 30) -> QPixmap:
    """A dark frame with one bright square blob centred on (cx, cy) in normalized
    coordinates — a stand-in for a beam."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.black)
    px, py = int(cx * w), int(cy * h)
    for y in range(max(0, py - r), min(h, py + r)):
        for x in range(max(0, px - r), min(w, px + r)):
            img.setPixel(x, y, 0xFFFFFFFF)
    return QPixmap.fromImage(img)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    is_t.QMessageBox = _FakeBox

    cam_names = ["C01-A-CAM1NF", "C02-B-CAM2NF", "C03-C-CAM3NF"]
    grid = is_t.MultiCameraGrid()
    grid.setup_cameras(cam_names)

    # Different sensor sizes on purpose: "the same place" has to mean the same
    # fraction of the frame, not the same pixel.
    sizes = [(640, 480), (320, 320), (800, 200)]
    blobs = [(0.25, 0.25), (0.75, 0.60), (0.50, 0.50)]
    for i, ((w, h), (bx, by)) in enumerate(zip(sizes, blobs)):
        iv = grid.get_img_view(i)
        iv.resize(w // 2, h // 2)         # no layout pass in a headless test
        iv.set_pixmap(_blob_pixmap(w, h, bx, by))

    v = _StubViewer(grid, cam_names)
    grid.overlay_edited.connect(v._on_overlay_edited)

    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    # ── 1. arming the drawing mode ────────────────────────────────────────────
    print("Draw mode reaches every selected camera")
    grid._on_cam_clicked(0)
    grid._on_cam_clicked(2)
    check(grid.selected_cam_indices() == [0, 2], "cameras 1 and 3 selected")
    v._toggle_draw_mode("cross")
    modes = [grid.get_img_view(i)._draw_mode for i in range(3)]
    check(modes == ["cross", "", "cross"], f"draw mode armed on both selected: {modes}")
    check(v.cb_cross.isChecked(), "Cross checkbox switched itself on")

    # ── 2. one click marks every selected camera at the same spot ─────────────
    print("A mark lands at the same place on every selected camera")
    src = grid.get_img_view(0)
    ir = src._img_rect()
    check(ir is not None and ir.width() > 0, "source tile has an image rect")
    mid = QPointF(ir.left() + ir.width() / 2.0, ir.top() + ir.height() / 2.0)
    src._set_cross_at(mid, ir)            # what a click in the middle does
    got = [grid.get_img_view(i).cross_pos_norm for i in range(3)]
    check(all(g is not None for g in (got[0], got[2])),
          "both selected cameras have a cross")
    check(got[1] is None, "the unselected camera has none")
    check(abs(got[0].x() - got[2].x()) < 1e-6 and abs(got[0].y() - got[2].y()) < 1e-6,
          f"same normalized position: {got[0].x():.3f},{got[0].y():.3f} vs "
          f"{got[2].x():.3f},{got[2].y():.3f}")
    check(abs(got[0].x() - 0.5) < 0.01 and abs(got[0].y() - 0.5) < 0.01,
          "the middle of the tile is the middle of the frame")

    # A circle already on camera 3 must survive a cross drag.
    other = grid.get_img_view(2)
    other.show_circle = True
    other.circle_center_norm = QPointF(0.2, 0.8)
    other.circle_rx_norm = other.circle_ry_norm = other.circle_r_norm = 0.1
    src._set_cross_at(QPointF(ir.left() + 5, ir.top() + 5), ir)
    check(other.circle_center_norm is not None
          and abs(other.circle_center_norm.x() - 0.2) < 1e-6,
          "a cross drag leaves the circle where it was")
    check(abs(other.cross_pos_norm.x() - grid.get_img_view(0).cross_pos_norm.x()) < 1e-6,
          "the cross followed to the top-left corner")

    # A tick box writes only its own shape: the panel's boxes show the LAST clicked
    # tile, so writing all three would hide a circle that only another selected
    # camera has.
    v.cb_circle.setChecked(False)          # panel shows "no circle"
    other.show_circle = True               # ... but camera 3 has one
    v.cb_cross.setChecked(False)
    v.cb_cross.setChecked(True)            # tick Cross
    check(other.show_circle, "ticking Cross leaves another camera's circle shown")

    # ── 3. calibration runs per camera, all at once ───────────────────────────
    print("Calibration calibrates each selected camera on its own frame")
    for i in range(3):
        grid.get_img_view(i).cross_pos_norm = None
    _FakeBox.calls = []
    v.calibrate_cross()
    pos = [grid.get_img_view(i).cross_pos_norm for i in range(3)]
    check(not _FakeBox.calls, f"no complaint dialog: {_FakeBox.calls}")
    check(pos[0] is not None and pos[2] is not None,
          "both selected cameras got a centroid")
    check(pos[1] is None, "the unselected camera was not touched")
    check(abs(pos[0].x() - blobs[0][0]) < 0.05 and abs(pos[0].y() - blobs[0][1]) < 0.05,
          f"camera 1 centroid on its own blob: {pos[0].x():.2f},{pos[0].y():.2f} "
          f"(expected {blobs[0][0]},{blobs[0][1]})")
    check(abs(pos[2].x() - blobs[2][0]) < 0.05 and abs(pos[2].y() - blobs[2][1]) < 0.05,
          f"camera 3 centroid on its own blob: {pos[2].x():.2f},{pos[2].y():.2f} "
          f"(expected {blobs[2][0]},{blobs[2][1]})")
    check(abs(pos[0].x() - pos[2].x()) > 0.05,
          "the two results differ — each camera was measured separately")

    # ── 4. nothing selected = every camera ────────────────────────────────────
    print("With nothing selected the controls act on all cameras")
    grid._on_cam_clicked(0)
    grid._on_cam_clicked(2)
    check(grid.selected_cam_indices() == [], "selection cleared")
    check(v._overlay_target_indices() == [0, 1, 2], "targets are all three cameras")

    print()
    if fails:
        print(f"{len(fails)} FAILED")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
