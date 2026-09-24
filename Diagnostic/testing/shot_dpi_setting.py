"""Screenshot the notification group of Settings, where the new
"Picture resolution (dpi)" row sits.

A row added to a form is exactly the change that looks fine in code and comes
out clipped or unreadable on screen.

    set QT_QPA_PLATFORM=windows
    python testing/shot_dpi_setting.py [out.png]

Offscreen rendering is not used on purpose - it has no fonts and lies about
text size.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as app_main  # noqa: E402
import monitor_tab as mt  # noqa: E402


def main(argv: list[str]) -> int:
    out = argv[1] if len(argv) > 1 else os.path.join(HERE, "dpi_setting.png")
    app = QApplication(sys.argv)
    # The same paint the program itself applies: without it this PC's Windows
    # dark mode leaves the labels dark-on-dark.
    app.setStyleSheet(app_main.APP_STYLESHEET)
    win = mt.MonitorWidget()
    dlg = mt.SettingsDialog(win)
    dlg.resize(980, 900)
    dlg.show()

    def shoot():
        target = dlg.chart_dpi.parentWidget() or dlg
        target.grab().save(out)
        print(f"saved {out}  ({target.width()}x{target.height()})")
        app.quit()

    QTimer.singleShot(700, shoot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
