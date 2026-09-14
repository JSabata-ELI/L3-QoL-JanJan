"""Screenshot the "ordering codes" window.

The codes are shown once and copied down by hand, so this is the one window
where unreadable text would cost real trouble - hence rendering it rather than
trusting the stylesheet.

    set QT_QPA_PLATFORM=windows
    python shot_order_codes.py [out.png]

The real settings file is never touched: the save is stubbed out.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

import okbase_menu as om  # noqa: E402

# Nothing may reach %APPDATA% from a screenshot script.
om.save_user_settings = lambda values: ""
om.load_user_settings = lambda: {}

import monitor_tab as mt  # noqa: E402


def main(argv: list[str]) -> int:
    out = argv[1] if len(argv) > 1 else os.path.join(HERE, "order_codes.png")
    app = QApplication(sys.argv)
    win = mt.MonitorWidget()
    dlg = mt.SettingsDialog(win)
    dlg.show()

    def shoot():
        # The codes window is modal, so it has to be grabbed from inside its
        # own event loop - hence the timer within a timer.
        def grab():
            for child in dlg.findChildren(QDialog):
                if child.isVisible():
                    child.grab().save(out)
                    print(f"saved {out} ({child.width()}x{child.height()})")
                    child.accept()
                    break
            app.quit()
        QTimer.singleShot(400, grab)
        dlg._okbase_make_codes()

    QTimer.singleShot(300, shoot)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
