"""Does the Areas table show the columns that MATTER at the default size?

Run:  python testing/render_areas_columns.py
Writes: testing/areas_columns.png and prints every column's width against the
room the table actually has.

The point of the harness: the first six columns are the area's own properties
and have to be readable without touching anything. Rectangle and Reference say
where it is, and those may sit past the right edge — the scrollbar is for them.
That is the operator's own ruling, so the verdict here is about the first six
and not about the sum.

Rendered on the real Windows platform plugin — offscreen has no fonts and lies
about text size. The settings are redirected to a throw-away copy first.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QEventLoop, QTimer                   # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

app = QApplication.instance() or QApplication([])

import ann_core as C                                            # noqa: E402
import ann_ui as U                                              # noqa: E402

_tmp = tempfile.mkdtemp(prefix="announcer_cols_")
_copy = Path(_tmp) / "presets.json"
_real = C.program_dir() / C.CONFIG_NAME
if _real.exists():
    _copy.write_bytes(_real.read_bytes())
C.config_path = lambda: _copy

U.install_app_look(app)

import ar_t                                                     # noqa: E402
import main                                                     # noqa: E402

win = main.AnnouncerWindow()
win.show()


def settle(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


settle(1200)
for i in range(win._tabs.count()):
    if win._tabs.tabText(i).strip().lower() == "areas":
        win._tabs.setCurrentIndex(i)
        break
settle(1200)

tab = win._tabs.currentWidget()
table = tab._table
# The first row selected, so the picture boxes on the right are photographed
# with something in them — that is where the black edge round a thumbnail has
# to be visible.
if table.rowCount():
    table.selectRow(0)
    settle(1500)
# The properties, up to and including "Fires". Rectangle and Reference are
# allowed to be past the edge.
MUST_SHOW = ar_t._C_FIRES

room = table.viewport().width()
print(f"window {win.width()}x{win.height()}   "
      f"table pane {table.width()} px, viewport {room} px")
right = 0
needed = 0
for col, name in enumerate(ar_t._COLS):
    w = table.columnWidth(col)
    right += w
    if col <= MUST_SHOW:
        needed = right
    edge = "" if right <= room else "  ← past the right edge"
    print(f"  {name:<20} {w:>4} px   ends at {right:>4}{edge}")
print(f"  {'the properties need':<20} {needed:>4} px of {room}")
print("VERDICT:", "the properties are all visible" if needed <= room
      else f"{needed - room} px too wide — a property column is cut off")

out = HERE / "areas_columns.png"
win.grab().save(str(out))
print(f"wrote {out.name}")
win.close()
