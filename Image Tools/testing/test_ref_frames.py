"""The "Reference frames" window: several frames per camera, nothing overwritten.

What this checks, on the real dialog (no exec(), nothing takes the keyboard focus):

* PCW3_NF opens with its hand-measured square as the only row;
* "Add by hand" and a read from the camera window ADD rows — the old ones keep
  their position, colour and name;
* a Show tick is saved at once, even when the window is then cancelled;
* renaming and deleting wait for OK, and Cancel puts everything back;
* the camera window is matched by the camera's short name, a picture file named
  after the camera is not, and no match gives the "not found on this computer"
  message without adding anything;
* the window and the read preview are rendered to PNGs for a look.

The settings file is redirected to a temp folder; the user's own is never touched.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"

import numpy as np                 # noqa: E402
import bench_common as B           # noqa: E402

FAILURES: "list[str]" = []
HERE = Path(__file__).resolve().parent

PCW3 = "C03-081-PCW3NF-_-IMG"
OTHER = "C03-040-PTM11WNF-_-IMG"


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def quiet(w):
    from PySide6.QtCore import Qt
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    return w


class FakeReader:
    """Stands in for is_window_read: one fake camera window, read for real."""

    def __init__(self, real, windows):
        self._real = real
        self._windows = windows
        img = np.full((600, 800, 3), 240, np.uint8)
        pic = np.zeros((512, 512, 3), np.uint8)
        pic[100:102, 100:400] = 255; pic[400:402, 100:400] = 255
        pic[100:402, 100:102] = 255; pic[100:402, 400:402] = 255
        img[40:552, 40:552] = pic
        self.image = img

    def list_windows(self):
        return list(self._windows)

    def find_window(self, title):
        for h, t in self._windows:
            if t == title:
                return h
        return None

    def read_window(self, hwnd, fw, fh, area=None):
        return self._real.read_image(self.image, fw, fh, area)

    def read_image(self, *a, **k):
        return self._real.read_image(*a, **k)


def main():
    m = B.load_slider()
    tmp = Path(tempfile.mkdtemp(prefix="ref_frames_test_"))
    m._CAM_REF_RECT_PATH = tmp / "cam_ref_rects.json"

    from PySide6.QtWidgets import QApplication, QMessageBox, QDialog
    from PySide6.QtGui import QPixmap, QColor
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication(sys.argv)

    real_reader = m._import_window_read()
    windows = [(101, "Camera C03-081 PCW3_NF - live"), (102, "PCW3NF.png - Photos"),
               (103, "Notepad")]
    fake = FakeReader(real_reader, windows)
    m._import_window_read = lambda: fake
    m.QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)

    view = quiet(m.ImageView())
    view.resize(500, 500)
    pm = QPixmap(1024, 1024); pm.fill(QColor(8, 0, 0))
    view.set_pixmap(pm)
    view.pdxm1_cam_name = PCW3

    print("\n-- opening on PCW3_NF --")
    dlg = quiet(m.CamRefRectDialog(PCW3, img_view=view))
    check("one row: the measured square", dlg._table.rowCount() == 1
          and dlg._frames[0].name == "Measured by hand")
    check("automatic window entry first", dlg._win_combo.itemText(0).startswith("Automatic"))

    print("\n-- add by hand, read from window: rows are ADDED --")
    first = m.CamRefRectConfig(**m._asdict(dlg._frames[0]))
    dlg._add_by_hand()
    check("hand frame added", len(dlg._frames) == 2 and dlg._frames[1].source == "hand")
    check("new frame has its own colour", dlg._frames[1].color != dlg._frames[0].color)

    # The read preview is checked separately; here it is accepted straight away.
    orig_exec = m.RefFrameReadDialog.exec
    m.RefFrameReadDialog.exec = lambda self: QDialog.DialogCode.Accepted
    dlg._read_window()
    check("window frame added", len(dlg._frames) == 3 and dlg._frames[2].source == "window",
          dlg._status.text())
    if len(dlg._frames) == 3:
        f = dlg._frames[2]
        check("read fractions are right",
              abs(f.left - 100.5 / 512) < 0.01 and abs(f.bottom - 400.5 / 512) < 0.01,
              f"{f.left:.4f} {f.top:.4f} {f.right:.4f} {f.bottom:.4f}")
        check("named with date and time", f.name.startswith("Camera window ") and f.read_at)
    dlg._read_window()
    check("a second read adds again", len(dlg._frames) == 4)
    check("first frame untouched",
          m._asdict(dlg._frames[0]) == m._asdict(first))

    print("\n-- the tile previews every shown frame --")
    check("preview carries all frames", len(view._ref_rect_override or []) == 4)

    print("\n-- render the window --")
    dlg._table.selectRow(2)
    dlg.show(); app.processEvents()
    dlg.grab().save(str(HERE / "ref_frames_dialog.png"))
    dlg.hide()

    print("\n-- OK saves everything --")
    dlg._on_accept(); dlg.save_config()
    saved = m.get_ref_frames(PCW3)
    check("four frames saved", len(saved) == 4, f"{len(saved)}")
    check("hand square saved unchanged", m._asdict(saved[0]) == m._asdict(first))

    print("\n-- Show tick saved at once, rename/delete undone by Cancel --")
    dlg2 = quiet(m.CamRefRectDialog(PCW3, img_view=view))
    dlg2._table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
    check("tick written at once", m.get_ref_frames(PCW3)[1].show is False)
    dlg2._table.item(0, 1).setText("Renamed")
    dlg2._table.selectRow(3)
    dlg2._delete()
    check("delete removes the row", len(dlg2._frames) == 3)
    dlg2.reject()
    after = m.get_ref_frames(PCW3)
    check("cancel keeps all four", len(after) == 4)
    check("cancel keeps the old name", after[0].name == "Measured by hand")
    check("cancel keeps the switched-off tick", after[1].show is False)
    check("tile back to the saved frames", view._ref_rect_override is None
          and len(view.ref_rect_cfg) == 4 and view.ref_rect_cfg[1].show is False)

    print("\n-- window matching --")
    hits = m.match_camera_windows(windows, "PCW3NF")
    check("camera window matched, picture file not", hits == [(101, windows[0][1])], str(hits))

    print("\n-- not found on this computer --")
    view.pdxm1_cam_name = OTHER
    fake._windows = [(103, "Notepad")]
    dlg3 = quiet(m.CamRefRectDialog(OTHER, img_view=view))
    check("other camera starts empty", dlg3._table.rowCount() == 0)
    dlg3._read_window()
    check("nothing added", dlg3._frames == [])
    check("says not found on this computer",
          "not found on this computer" in dlg3._status.text(), dlg3._status.text())
    dlg3.show(); app.processEvents()
    dlg3.grab().save(str(HERE / "ref_frames_not_found.png"))
    dlg3.hide()
    dlg3.reject()

    print("\n-- the read preview --")
    m.RefFrameReadDialog.exec = orig_exec
    res = real_reader.read_image(fake.image, 1024, 1024)
    pv = quiet(m.RefFrameReadDialog(res, 1024, 1024, "Camera C03-081 PCW3_NF - live",
                                    real_reader, dlg))
    check("preview offers to add", pv._add_btn.isEnabled() and pv.fractions is not None)
    pv.show(); app.processEvents()
    pv.grab().save(str(HERE / "ref_frames_preview.png"))
    pv._on_area_drawn((40, 40, 552, 552))
    check("hand area reads again", pv.hand_area == (40, 40, 552, 552)
          and pv.fractions is not None)
    pv.hide()

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
