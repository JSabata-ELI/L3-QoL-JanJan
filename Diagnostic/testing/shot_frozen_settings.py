"""Screenshot the "not updating" part of the Settings window.

The row "Not updating after (min)" was dropped when the check stopped judging
PVs by a repeating value, and removing a row from a form is exactly the change
that leaves a stray label or a clipped tooltip behind. So this renders it.

    set QT_QPA_PLATFORM=windows
    python testing/shot_frozen_settings.py [out.png]

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
import main as app_main  # noqa: E402


def main(argv: list[str]) -> int:
    out = argv[1] if len(argv) > 1 else os.path.join(HERE, "frozen_settings.png")
    app = QApplication(sys.argv)
    # The same paint the program itself applies: without it this PC's Windows
    # dark mode leaves every checkbox label dark-on-dark, which says nothing
    # about how the dialog really looks.
    app.setStyleSheet(app_main.APP_STYLESHEET)
    win = mt.MonitorWidget()
    dlg = mt.SettingsDialog(win)
    dlg.resize(980, 900)
    dlg.show()

    def shoot():
        # Grab the whole group the two switches live in: the window is far
        # taller than it, and a leftover empty form row would show up here.
        target = dlg.frozen_en.parentWidget() or dlg
        target.grab().save(out)
        print(f"saved {out}  ({target.width()}x{target.height()})")
        app.quit()

    QTimer.singleShot(700, shoot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
