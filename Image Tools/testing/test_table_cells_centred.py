"""Every cell of Workshop's table windows is centred.

The Beam report used to right-align the cells that looked like numbers and leave the
rest on the left, so "175.0" sat on the right edge of the Value column while
"272.0 px = 1.197 mm" sat on the left one. Every cell, words and numbers alike, is now
centred — Beam report, Results table, Radial profile, Encircled energy and Histogram
numbers all use the same table window.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt                           # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

import wk_t                                             # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_every_cell_centred():
    dlg = wk_t._TableDialog("Beam report")
    rows = [["Measured on", "selected region", ""],
            ["Background taken off", "175.0", "pixel intensity"],
            ["D4σ width across", "272.0 px  =  1.197 mm", ""],
            ["Long axis tilt", "31.09", "° anticlockwise"],
            ["FWHM down", "not reached", ""]]
    dlg.set_content(["Quantity", "Value", "Unit"], rows)
    want = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
    t = dlg._table
    for r in range(t.rowCount()):
        for c in range(t.columnCount()):
            got = int(t.item(r, c).textAlignment())
            assert got == want, (rows[r][c], hex(got))
    dlg.deleteLater()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
    print("all ok")
