"""Render the Save view dialog UNDER main.py's app stylesheet, and look at it.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_save_view_dialog.py

A QDialog is a plain QWidget, and the app stylesheet paints those light grey with
dark ink — so a style meant for the dark PV sidebar comes out white-on-near-white
there, and `_CHECKBOX_STYLE*` does nothing at all to a radio button. Offscreen has
no fonts and cannot be trusted for this; render it properly.

Writes `save_view_dialog.png` and `pv_edit_dialog.png` beside this file.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_finder_moment import load_finder


def app_stylesheet() -> str:
    """The stylesheet main.py puts on the application, read out of main.py itself
    so this cannot drift from what the program really does."""
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(
        encoding="utf-8", errors="ignore")
    # The APPLICATION's stylesheet, not the first setStyleSheet in the file — the
    # first one belongs to an error label and taking it gave a bare dark dialog and
    # a wrong verdict.
    marker = 'app.setStyleSheet("""'
    a = src.find(marker)
    if a < 0:
        return ""
    a += len(marker)
    b = src.find('"""', a)
    return src[a:b] if b > a else ""


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
        print(f"app stylesheet: {len(css)} chars")
    else:
        print("WARNING: could not read main.py's stylesheet — rendering bare")

    dlg = m._SaveViewDialog(3)
    dlg.resize(360, 260)
    dlg.show()
    app.processEvents()
    out = Path(__file__).with_name("save_view_dialog.png")
    dlg.grab().save(str(out))
    print("written:", out)
    dlg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
