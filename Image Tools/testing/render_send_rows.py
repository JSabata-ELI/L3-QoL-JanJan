"""Render the "send this on to another tab" rows at the real panel widths.

Three buttons on one 280 px row is the tightest of them, so the labels are
measured rather than guessed. Offscreen has no fonts and lies about text size, so
this needs a real platform:

    set QT_QPA_PLATFORM=windows
    python testing/render_send_rows.py

Writes testing/send_rows.png and prints, for every button, how much wider its
text is than the room it got — a positive number means the label is cut off.
The real stylesheets are imported from the tabs, so a change there is caught here.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,  # noqa: E402
                               QPushButton, QLabel)

import if_t                                            # noqa: E402
import is_t                                            # noqa: E402
import sf_t                                            # noqa: E402

# main.py's app stylesheet — the buttons are drawn on top of exactly this.
APP_QSS = """
    QWidget      { background: #f3f3f3; color: #111; }
    QLabel       { background: transparent; }
    QPushButton  { padding: 5px 8px; }
"""

# (tab, panel width, [labels]) — the width each tab's left panel really has.
ROWS = [
    ("Image Finder  (268 px panel)", 268, if_t._SEND_BTN_QSS,
     ["➤ Image Slider", "➤ Workshop"]),
    ("Image Slider  (275 px panel)", 275, is_t._SEND_BTN_QSS,
     ["➤ Image Finder", "➤ Workshop"]),
    ("Shot Finder  (280 px panel)", 280, sf_t._SEND_BTN_QSS,
     ["➤ Image Finder", "➤ Image Slider", "➤ Workshop"]),
]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)

    root = QWidget()
    root.setFixedWidth(300)
    lay = QVBoxLayout(root)
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(10)

    made = []
    for title, width, qss, labels in ROWS:
        cap = QLabel(title)
        cap.setStyleSheet("font-size: 10px; color: #333;")
        lay.addWidget(cap)
        holder = QWidget()
        # The panel width minus the section body margins, which is the room a row
        # in the running tab actually gets.
        holder.setFixedWidth(width - 28)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3 if len(labels) > 2 else 4)
        for i, text in enumerate(labels):
            b = QPushButton(text)
            b.setStyleSheet(qss)
            # One of each row is greyed, so the disabled ink is on the picture too.
            b.setEnabled(i != len(labels) - 1)
            row.addWidget(b, 1)
            made.append((title, b))
        lay.addWidget(holder)

    root.show()
    app.processEvents()

    out = HERE / "send_rows.png"
    root.grab().save(str(out))
    print("wrote", out)
    worst = 0
    for title, b in made:
        fm = b.fontMetrics()
        need = fm.horizontalAdvance(b.text())
        # 8 px for the two 4 px paddings, 2 px for the border.
        room = b.width() - 10
        over = need - room
        worst = max(worst, over)
        print(f"  {title:<30} {b.text():<16} w={b.width():>4} px  "
              f"text={need:>4} px  room={room:>4} px  "
              f"{'CUT by ' + str(over) if over > 0 else 'fits'}")
    print("worst overflow:", worst, "px")
    return 0 if worst <= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
