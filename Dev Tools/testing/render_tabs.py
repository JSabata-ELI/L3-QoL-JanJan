"""Screenshot both tabs on the real (practice) setup, without taking focus.

Run:  py testing/render_tabs.py <output folder>
The window is shown with WA_ShowWithoutActivating and never activated.
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import dt_core as core  # noqa: E402
from dt_common import apply_app_style, init_ui_thread  # noqa: E402


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    apply_app_style(app)
    app.setWindowIcon(QIcon(str(core.icon_file())))
    init_ui_thread()
    import dev_tools
    w = dev_tools.DevToolsWindow()
    w.setAttribute(Qt.WA_ShowWithoutActivating, True)
    w.setWindowFlag(Qt.WindowDoesNotAcceptFocus, True)
    w.resize(1400, 900)
    w.move(40, 40)
    w.show()

    def shoot():
        # A few ticks, so the picture shows both tick box states. Only the
        # screen state changes; nothing is built or copied.
        for name in ("Image Tools", "Diagnostic"):
            if name in w.builder.items:
                w.builder.items[name].setCheckState(1, Qt.Checked)
        for name in ("CSS Logger", "Diagnostic"):
            if name in w.copy.rows:
                w.copy.rows[name]["item"].setCheckState(0, Qt.Checked)
        w.tabs.setCurrentIndex(0)
        app.processEvents()
        w.grab().save(str(out / "builder.png"))
        w.tabs.setCurrentIndex(1)
        app.processEvents()
        w.grab().save(str(out / "copy.png"))
        app.quit()

    QTimer.singleShot(1500, shoot)
    app.exec()


if __name__ == "__main__":
    main(Path(sys.argv[1]))
