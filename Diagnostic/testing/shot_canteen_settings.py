"""Screenshot the canteen part of the Settings window.

A field added to a form is exactly the change that looks fine in code and comes
out unreadable or clipped on screen, so this renders it: it opens the real
Settings window, scrolls to the canteen group and saves a PNG.

    set QT_QPA_PLATFORM=windows
    python shot_canteen_settings.py [out.png]

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

import monitor_tab as mt  # noqa: E402


def main(argv: list[str]) -> int:
    out = argv[1] if len(argv) > 1 else os.path.join(HERE, "canteen_settings.png")
    app = QApplication(sys.argv)
    win = mt.MonitorWidget()
    dlg = mt.SettingsDialog(win)
    dlg.resize(980, 900)
    dlg.show()

    def shoot():
        # Bring the canteen group into view, then keep only that part: the
        # window is far taller than the group and the rest is not the change.
        box = dlg.okbase_account.parentWidget()
        while box is not None and not isinstance(box, type(dlg.okbase_en.parent())):
            box = box.parentWidget()
        target = dlg.okbase_account.parentWidget() or dlg
        target.grab().save(out)
        print(f"saved {out} ({target.width()}x{target.height()})")
        app.quit()

    QTimer.singleShot(1500, shoot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
