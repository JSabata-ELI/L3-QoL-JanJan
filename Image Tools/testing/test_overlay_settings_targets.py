"""Overlay Settings: one section per kind, "Apply to" drives every setting.

Claims:
  1. The window has a Crosses, a Circles and a Squares section, each starting with
     "Apply to", and no "Apply to all cameras" box any more.
  2. No made-up limits: thickness, size and outline take values far past the old
     20 / 200 / 10, and the "max ~" hint is gone.
  3. "Cross 2" + OK changes only cross 2 (colour, thickness, size) — cross 1 keeps
     the kind's style — and the look travels with the mark (mark_look), so it is
     drawn and survives copy_marks.
  4. "All crosses – all cameras" + OK sets the style on every camera and on the
     panel (the saved pictures), and drops the own look of the marks.
  5. Width / height of a square are set in image pixels about its centre.
  6. Switching "Apply to" keeps what was typed for the previous choice.
  7. Cancel puts every mark and every camera's style back.

Runs headless:  python testing/test_overlay_settings_targets.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="it_ovl_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer, Qt                       # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap           # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,  # noqa: E402
                               QDialog, QGroupBox, QLabel, QSpinBox, QWidget)

import is_t                                                 # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


class _StubViewer(QWidget):
    """Just enough of Viewer (a real widget, as the dialog's parent) to run the
    real Overlay Settings method against a real camera grid. Never shown."""
    _is_multi_cam      = is_t.Viewer._is_multi_cam
    _overlay_target_indices = is_t.Viewer._overlay_target_indices
    _overlay_targets   = is_t.Viewer._overlay_targets
    _overlay_style     = is_t.Viewer._overlay_style
    _open_overlay_settings = is_t.Viewer._open_overlay_settings

    def __init__(self, grid):
        super().__init__()
        self._multi_grid = grid
        self._cam_names = list(grid._cam_names_list)
        self.img_view = is_t.ImageView()
        st = is_t.mark_style_from_json(None)
        self._overlay_cross_color = st["cross_color"]
        self._overlay_cross_thick = st["cross_thick"]
        self._overlay_cross_size = st["cross_size"]
        self._overlay_circle_color = st["circle_color"]
        self._overlay_circle_thick = st["circle_thick"]
        self._overlay_square_color = st["square_color"]
        self._overlay_square_thick = st["square_thick"]
        self._overlay_outline = {k: {"on": st[f"{k}_outline"],
                                     "color": st[f"{k}_outline_color"],
                                     "w": st[f"{k}_outline_w"]} for k in is_t.MARK_KINDS}
        self._ui_state = {}
        self.saved = 0

    def _save_ui_state(self):
        self.saved += 1


def _frame(w, h) -> QPixmap:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.black)
    return QPixmap.fromImage(img)


def _dialog():
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QDialog) and w.windowTitle() == "Overlay Settings" \
                and w.isVisible():
            return w
    return None


def _section(dlg, title):
    for b in dlg.findChildren(QGroupBox):
        if b.title() == title:
            return b
    return None


def _field(box, label):
    """The widget on the form row whose label is `label`."""
    lay = box.layout()
    for r in range(lay.rowCount()):
        it = lay.itemAt(r, lay.ItemRole.LabelRole)
        if it is not None and it.widget() is not None and it.widget().text() == label:
            return lay.itemAt(r, lay.ItemRole.FieldRole).widget()
    return None


def run_dialog(v, drive):
    """Open the real dialog; `drive(dlg)` fills it in and accepts or rejects."""
    def go():
        dlg = _dialog()
        if dlg is None:
            QTimer.singleShot(50, go)
            return
        try:
            drive(dlg)
        except Exception as e:                       # never leave exec() hanging
            FAILURES.append(f"drive raised {e!r}")
            dlg.reject()
    QTimer.singleShot(50, go)
    v._open_overlay_settings()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    names = ["C01-A-CAM1NF", "C02-B-CAM2NF"]
    grid = is_t.MultiCameraGrid()
    grid.setup_cameras(names)
    for i in range(2):
        iv = grid.get_img_view(i)
        iv.resize(320, 240)
        iv.set_pixmap(_frame(1000, 800))
        iv.marks["cross"] = [(0.2, 0.2), (0.6, 0.6)]
        iv.marks["square"] = [(0.4, 0.4, 0.6, 0.6)]
    v = _StubViewer(grid)
    iv0, iv1 = grid.get_img_view(0), grid.get_img_view(1)

    print("Layout of the window")
    seen = {}

    def look(dlg):
        seen["boxes"] = [b.title() for b in dlg.findChildren(QGroupBox)]
        seen["apply"] = all(_field(_section(dlg, t), "Apply to:") is not None
                            for t in ("Crosses", "Circles", "Squares"))
        seen["all_box"] = any(c.text() == "Apply to all cameras"
                              for c in dlg.findChildren(QCheckBox))
        seen["hint"] = any("max ~" in l.text() for l in dlg.findChildren(QLabel))
        cr = _section(dlg, "Crosses")
        seen["items"] = [_field(cr, "Apply to:").itemText(i)
                         for i in range(_field(cr, "Apply to:").count())]
        seen["max"] = min(sb.maximum() for sb in dlg.findChildren(QSpinBox))
        dlg.reject()
    run_dialog(v, look)
    check("three sections", seen.get("boxes") == ["Crosses", "Circles", "Squares"],
          str(seen.get("boxes")))
    check("each starts with Apply to", seen.get("apply"))
    check("no 'Apply to all cameras' box", not seen.get("all_box"))
    check("no 'max ~' hint", not seen.get("hint"))
    check("cross choices", seen.get("items") == ["All crosses – all cameras",
                                                   "Cross 1", "Cross 2"],
          str(seen.get("items")))
    check("no small upper limit on any number", seen.get("max", 0) >= 100000,
          str(seen.get("max")))

    print("Cross 2 only")

    def cross2(dlg):
        cr = _section(dlg, "Crosses")
        combo = _field(cr, "Apply to:")
        combo.setCurrentIndex(combo.findText("Cross 2"))
        col = _field(cr, "Color:")
        col.set_color(QColor("#ff0000"))
        _field(cr, "Thickness:").setValue(37)       # old limit was 20
        _field(cr, "Size:").setValue(450)           # old limit was 200
        dlg.accept()
    run_dialog(v, cross2)
    for iv in (iv0, iv1):
        lk1 = is_t.mark_look("cross", iv.marks["cross"][0])
        lk2 = is_t.mark_look("cross", iv.marks["cross"][1])
        check("cross 1 keeps the kind's style", lk1 == {}, str(lk1))
        check("cross 2 has its own look",
              lk2.get("thick") == 37 and lk2.get("size") == 450
              and QColor(lk2.get("color")).name() == "#ff0000", str(lk2))
    check("geometry untouched", is_t.mark_core("cross", iv0.marks["cross"][1]) == (0.6, 0.6))
    check("the panel style did not change", v._overlay_cross_thick == 2)
    copied = is_t.copy_marks(iv0.marks)
    check("look survives copy_marks", is_t.mark_look("cross", copied["cross"][1])
          .get("thick") == 37)
    # Drawn with it: a red pixel on cross 2's arm, none on cross 1's.
    img = QImage(1000, 800, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.black)
    from PySide6.QtGui import QPainter
    from PySide6.QtCore import QRect
    p = QPainter(img)
    is_t.draw_marks(p, QRect(0, 0, 1000, 800), iv0.marks, v._overlay_style())
    p.end()
    c2 = QColor(img.pixel(600 + 300, 480))     # 300 px out on the 450 px arm
    c1 = QColor(img.pixel(200 + 10, 160))
    check("cross 2 drawn red and long", c2.red() > 200 and c2.green() < 60, c2.name())
    check("cross 1 drawn in the kind's green", c1.green() > 200 and c1.red() < 60,
          c1.name())

    print("Squares: width / height in image pixels")

    def sq_size(dlg):
        sq = _section(dlg, "Squares")
        combo = _field(sq, "Apply to:")
        combo.setCurrentIndex(combo.findText("Square 1"))
        seen["w0"] = _field(sq, "Width:").value()
        _field(sq, "Width:").setValue(500)
        _field(sq, "Height:").setValue(80)
        dlg.accept()
    run_dialog(v, sq_size)
    check("width read in image px", seen.get("w0") == 200, str(seen.get("w0")))
    l, t, r, b = is_t.mark_core("square", iv0.marks["square"][0])
    check("new size about the same centre",
          abs((r - l) * 1000 - 500) < 1e-6 and abs((b - t) * 800 - 80) < 1e-6
          and abs((l + r) / 2 - 0.5) < 1e-9 and abs((t + b) / 2 - 0.5) < 1e-9,
          f"{(l, t, r, b)}")

    print("Switching the choice keeps what was typed")

    def switch(dlg):
        sq = _section(dlg, "Squares")
        combo = _field(sq, "Apply to:")
        combo.setCurrentIndex(combo.findText("Square 1"))
        _field(sq, "Thickness:").setValue(9)
        combo.setCurrentIndex(0)                         # all cameras
        seen["all_thick"] = _field(sq, "Thickness:").value()
        dlg.reject()                                     # and Cancel undoes it all
    before = list(iv0.marks["square"])
    run_dialog(v, switch)
    check("the other choice shows its own values", seen.get("all_thick") == 2,
          str(seen.get("all_thick")))
    check("Cancel put the square back", iv0.marks["square"] == before)

    def switch_ok(dlg):
        sq = _section(dlg, "Squares")
        combo = _field(sq, "Apply to:")
        combo.setCurrentIndex(combo.findText("Square 1"))
        _field(sq, "Thickness:").setValue(9)
        combo.setCurrentIndex(0)
        dlg.accept()
    run_dialog(v, switch_ok)
    check("the typed thickness was kept for Square 1",
          is_t.mark_look("square", iv0.marks["square"][0]).get("thick") == 9)

    print("All crosses – all cameras")

    def all_cams(dlg):
        cr = _section(dlg, "Crosses")
        _field(cr, "Color:").set_color(QColor("#0000ff"))
        _field(cr, "Thickness:").setValue(5)
        dlg.accept()
    run_dialog(v, all_cams)
    check("panel style changed (saved pictures)", v._overlay_cross_thick == 5
          and v._overlay_cross_color.name() == "#0000ff")
    check("every camera got it", all(iv.cross_thickness == 5 for iv in (iv0, iv1)))
    check("own looks dropped", all(is_t.mark_look("cross", g) == {}
                                   for iv in (iv0, iv1) for g in iv.marks["cross"]))
    check("style saved", "mark_style" in v._ui_state and v.saved >= 1)

    print("Cancel restores camera styles")

    def cancel(dlg):
        cr = _section(dlg, "Crosses")
        _field(cr, "Thickness:").setValue(77)
        _field(cr, "Apply to:").setCurrentIndex(1)       # commits to all cameras
        dlg.reject()
    run_dialog(v, cancel)
    check("camera style back after Cancel", iv0.cross_thickness == 5,
          str(iv0.cross_thickness))
    check("panel style untouched by Cancel", v._overlay_cross_thick == 5)

    print()
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
