"""Grab the "send this on" rows out of the REAL panels of the three tabs.

render_send_rows.py measures the labels at the real widths; this one takes the
picture of the running panels, which is the only way to see what the section
margins, the neighbouring widgets and the app stylesheet actually leave for them.

Offscreen has no fonts and lies about text size, so this needs a real platform:

    set QT_QPA_PLATFORM=windows
    python testing/render_send_rows_real.py

Writes testing/send_row_finder.png, send_row_slider.png and send_row_shot.png.
Nothing is read from the archiver or the share; no button is pressed.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication            # noqa: E402

APP_QSS = """
    QWidget      { background: #f3f3f3; color: #111; }
    QLabel       { background: transparent; }
    QPushButton  { padding: 5px 8px; }
"""


def shot(buttons, out: Path, title: str):
    """The row of buttons with a little of the panel around it."""
    r = buttons[0].geometry()
    for b in buttons[1:]:
        r = r.united(b.geometry())
    buttons[0].parentWidget().grab(r.adjusted(-8, -8, 8, 8)).save(str(out))
    print(f"wrote {out}")
    for b in buttons:
        print(f"  {title:<14} {b.text():<16} w={b.width():>4} px  "
              f"enabled={b.isEnabled()}")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)

    import if_t
    import is_t
    import sf_t

    fi = if_t.ImageFinderWidget(); fi.resize(1400, 900); fi.show()
    v = is_t.Viewer();             v.resize(1400, 900);  v.show()
    sh = sf_t.ShotFinderWidget();  sh.resize(1400, 900); sh.show()
    app.processEvents()

    shot([fi._btn_send_slider, fi._btn_send_workshop],
         HERE / "send_row_finder.png", "Image Finder")
    shot([v.btn_send_finder, v.btn_send_workshop],
         HERE / "send_row_slider.png", "Image Slider")
    shot([sh._btn_open_finder, sh._btn_open_slider, sh._btn_send_workshop],
         HERE / "send_row_shot.png", "Shot Finder")
    return 0


if __name__ == "__main__":
    sys.exit(main())
