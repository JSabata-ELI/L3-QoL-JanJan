"""Pictures of the 20.10.2026 changes, to check them by eye with real fonts:
the Overlay Settings window, the layout tab bar (selected tab) with its right-click
menu, and the PV list with its master eye.

    QT_QPA_PLATFORM=minimal:enable_fonts python testing/render_overlay_tabs_eye.py [out_dir]

Nothing is put on screen (minimal platform), so no window takes the focus.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="it_render_")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QTimer                            # noqa: E402
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

import is_t                                                  # noqa: E402
import slider_tabs                                           # noqa: E402
from render_display_rows import app_stylesheet               # noqa: E402
import test_overlay_settings_targets as ovl                  # noqa: E402
import test_slider_tab_menu as tabt                          # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet())

    # Overlay Settings, two cameras with marks.
    grid = is_t.MultiCameraGrid()
    grid.setup_cameras(["C01-A-CAM1NF", "C02-B-CAM2NF"])
    for i in range(2):
        iv = grid.get_img_view(i)
        iv.set_pixmap(ovl._frame(1000, 800))
        iv.marks["cross"] = [(0.2, 0.2), (0.6, 0.6)]
        iv.marks["square"] = [(0.4, 0.4, 0.6, 0.6)]
    v = ovl._StubViewer(grid)

    def shoot(dlg):
        dlg.adjustSize()
        dlg.grab().save(str(out / "overlay_settings.png"))
        dlg.reject()
    ovl.run_dialog(v, shoot)

    # Layout tab bar: three tabs, the middle one selected.
    tabs = slider_tabs.SliderTabs(tabt._FakeViewer)
    tabs.set_outer_visible(True)
    tabs.new_tab("Ghost cameras")
    tabs.new_tab("Laser running")
    tabs._tabs.setCurrentIndex(1)
    host = QWidget()
    host.setStyleSheet("QWidget { background: #f3f3f3; }")
    lay = QVBoxLayout(host)
    lay.addWidget(tabs._tabs.chrome)
    host.resize(520, 40)
    host.grab().save(str(out / "layout_tabs.png"))
    menu = tabs._tab_menu(1)
    menu.adjustSize()
    menu.grab().save(str(out / "layout_tab_menu.png"))

    # PV list, one PV hidden.
    tbl = is_t.PvValueTable()
    tbl.refresh(["PV_A", "PV_B", is_t.SHOT_ROW_KEY], {"PV_B"},
                lambda n: ("12.3", False, ""))
    tbl.resize(275, tbl.height())
    tbl.grab().save(str(out / "pv_table_eye.png"))
    tbl.refresh(["PV_A", "PV_B", is_t.SHOT_ROW_KEY], {"PV_A", "PV_B", is_t.SHOT_ROW_KEY},
                lambda n: ("12.3", False, ""))
    tbl.grab().save(str(out / "pv_table_eye_all_hidden.png"))
    print(f"saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
