"""Render check: the Folder column of the Shot Finder results table.

The share path starts "\\\\users-L3...", and with Qt's default word wrap the
cell broke after the hyphen and showed only "\\\\users-" however wide the column
was. This renders one ordinary row and one two-line PV row with the real path
and prints what the Folder cell will draw.

Run:  python testing/render_sf_results_folder.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication, QTableWidgetItem   # noqa: E402

import sf_t                                                    # noqa: E402

app = QApplication([])
app.setStyle("Fusion")

w = sf_t.ShotFinderWidget()
t = w._make_results_table()
path = (r"\\users-L3.tier0.lcs.local\cpva-image-2026\2026-10-08\09"
        r"\L3-OM1FF-CAM-01")
rows = [
    ["2026-10-08", "✓", "09:12:33.120", "SBW4", "20.11 J", "0.1100 J",
     "✓ 12 values in range", path],
    ["2026-10-09", "✓", "10:40:01.500", "SBW4\nPTM1", "19.9 J\n3.2 J",
     "0.1 J\n0.2 J", "✓ match", path],
]
for r, cells in enumerate(rows):
    t.insertRow(r)
    for c, text in enumerate(cells):
        t.setItem(r, c, QTableWidgetItem(text))
t.setRowHeight(1, 18 * 2 + 8)
print(f"word wrap: {t.wordWrap()}")
need = t.fontMetrics().horizontalAdvance(path) + 12
# Wide window, and the narrower one a result pane really gets beside the panel.
for width in (1100, 800):
    t.resize(width, 160)
    app.processEvents()
    out = HERE / f"sf_results_folder_{width}.png"
    t.grab().save(str(out))
    fw = t.columnWidth(sf_t.RES_COL_FOLDER)
    hbar = t.horizontalScrollBar()
    print(f"{width} px table: folder column {fw} px, path needs ~{need} px "
          f"-> {'fits' if fw >= need else 'needs scroll'}; "
          f"side scroll {'on' if hbar.maximum() > 0 else 'off'}  ({out.name})")

# The operator drags two columns narrower: they keep that width, even when the
# text no longer fits, and new rows do not undo it. A double-click on the edge
# hands the column back to the automatic fit.
hh = t.horizontalHeader()
auto_status = t.columnWidth(sf_t.RES_COL_STATUS)
hh.resizeSection(sf_t.RES_COL_STATUS, 60)
hh.resizeSection(sf_t.RES_COL_FOLDER, 120)
t.insertRow(2)
t.setItem(2, sf_t.RES_COL_STATUS, QTableWidgetItem("✓ 1234 values in range"))
for _ in range(3):
    app.processEvents()
ok_drag = (t.columnWidth(sf_t.RES_COL_STATUS) == 60
           and t.columnWidth(sf_t.RES_COL_FOLDER) == 120)
print(f"dragged widths kept after new rows: {'yes' if ok_drag else 'NO'} "
      f"(Status {t.columnWidth(sf_t.RES_COL_STATUS)}, "
      f"Folder {t.columnWidth(sf_t.RES_COL_FOLDER)})")
out = HERE / "sf_results_dragged.png"
t.grab().save(str(out))
hh.sectionHandleDoubleClicked.emit(sf_t.RES_COL_STATUS)
for _ in range(3):
    app.processEvents()
back = t.columnWidth(sf_t.RES_COL_STATUS)
print(f"double-click gives the fit back: {'yes' if back >= auto_status else 'NO'} "
      f"({back} px, was {auto_status} px)  ({out.name})")
