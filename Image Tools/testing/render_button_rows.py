"""Render the paired button rows at the real sidebar width (275 px) so clipping
of the longest labels can be seen instead of guessed.

    set QT_QPA_PLATFORM=windows
    python testing/render_button_rows.py

Writes testing/button_rows.png and prints, for every button, how much wider its
text is than the room it got (a positive number = the label is cut).
"""
import sys
from pathlib import Path

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QPushButton, QLabel)
from PySide6.QtGui import QFontMetrics

APP_QSS = """
    QWidget      { background: #f3f3f3; color: #111; }
    QLabel       { background: transparent; }
    QPushButton  { padding: 5px 8px; }
"""

ROWS = [
    ("⤢ Reset zoom", "⛶ Reset layout"),
    ("💾 Save Plot", "〰 Show Path"),
    ("🗑 Delete mode", "↶ Undo delete"),
    ("✕ Close graph", "↺ Restore All"),
]


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)

    root = QWidget()
    root.setFixedWidth(275)
    lay = QVBoxLayout(root)
    # Margins chosen so the buttons come out 125 px wide, which is what they measure
    # in the running panel (275 px sidebar, section body margins, 4 px row spacing).
    lay.setContentsMargins(10, 8, 10, 8)
    lay.setSpacing(6)

    buttons = []
    for a, b in ROWS:
        row = QHBoxLayout()
        row.setSpacing(4)
        for text in (a, b):
            btn = QPushButton(text)
            if text.endswith("Delete mode"):
                # its armed state, the one that has to stay readable
                btn.setStyleSheet("background-color: #c62828; color: white; "
                                  "font-weight: bold; padding: 5px 8px;")
            row.addWidget(btn, 1)
            buttons.append(btn)
        lay.addLayout(row)
    lay.addWidget(QLabel("(rendered at the real 275 px sidebar width)"))

    root.show()
    app.processEvents()

    for btn in buttons:
        fm = QFontMetrics(btn.font())
        need = fm.horizontalAdvance(btn.text()) + 16   # padding 5px 8px, both sides
        over = need - btn.width()
        flag = "CLIPPED" if over > 0 else "ok"
        label = btn.text().encode("ascii", "replace").decode("ascii")
        print(f"{label:<20} width={btn.width():>4}  needs={need:>4}  {flag}")

    out = Path(__file__).with_name("button_rows.png")
    root.grab().save(str(out))
    print("saved", out)


if __name__ == "__main__":
    main()
