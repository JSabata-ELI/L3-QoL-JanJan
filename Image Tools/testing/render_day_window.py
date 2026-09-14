"""Render check: the window a double-clicked day opens, and the progress bar.

Rendered on the real Windows platform plugin — offscreen has no fonts and lies about
text size. Two pictures come out: the shot window (list + the day's curve) and the left
panel with the bar mid-search.

Run:  python testing/render_day_window.py
"""
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication          # noqa: E402

import sf_t                                          # noqa: E402

DAY = date(2026, 9, 2)
CAM = "C02-101-SHG-NF"
COL = "sbw4"


def rows(n):
    out = []
    for i in range(n):
        dt = datetime(DAY.year, DAY.month, DAY.day, 8 + i // 4, 15 * (i % 4))
        ns = int(dt.replace(tzinfo=timezone.utc).timestamp() * 1e9)
        out.append({"_dt": dt, "_ns": ns, COL: f"{10.0 + 0.03 * i:.3f}"})
    return out


def result(n=14):
    rr = rows(n)
    return {"day": DAY, "cam": CAM, "status": "ok",
            "best_row": rr[3], "rows_in_tol": rr,
            "col": COL, "actual": 10.09, "diff": 0.09, "target_csv": 10.0,
            "hour_folder": None, "per_col": {COL: rr},
            "search_cols": [COL], "extra_cols": [],
            "criteria_csv": [{"col": COL, "target_csv": 10.0, "tol_ui": 1.0}],
            "col_meta": {COL: {"source": "api", "status": "ok"}},
            "img_path": None, "folder_path": None, "display_vals": {}, "reason": ""}


app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")
app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                  "QLabel { background: transparent; }")

w = sf_t.ShotFinderWidget()


def fake_find(dr, cam, dt_obj, ts_ns, hour_cache, scan_cache=None):
    """The frame of a shot, answered without the share — so the picture shows the
    Frame time column as it really looks, filled, and the render never waits on a
    network folder."""
    ns = int(ts_ns or 0) + 5_000_000
    return Path(f"//share/2026/9/2/6/{CAM}/{CAM}-_-IMG_-_{ns}.png")


w._find_image_for_shot = fake_find
w._load_and_show_preview = lambda *a, **k: None
w.resize(1500, 850)
w.show()
app.processEvents()

w._rebuild_result_tabs([CAM])
w._on_day_result(result())
app.processEvents()
w._table.selectRow(0)
w._on_table_double_clicked(w._table.model().index(0, 0))
for _ in range(30):
    app.processEvents()

out = HERE / "day_window.png"
w._day_window.grab().save(str(out))
print(f"screenshot: {out}   size={w._day_window.width()}x{w._day_window.height()}")
print(f"title: {w._day_window.windowTitle()}")
print(f"shot rows: {w._day_table.rowCount()}   "
      f"columns: {[w._day_table.horizontalHeaderItem(c).text() for c in range(w._day_table.columnCount())]}")

# The bar, half way through a two-day two-camera search.
w._prog_take("search", 4, "reading PV data · 2026-09-02 · 2/3 PV(s)")
w._prog_hits = 2
w._on_progress(1.4)
app.processEvents()
panel = w._prog.parentWidget()
out2 = HERE / "day_progress.png"
panel.grab().save(str(out2))
print(f"screenshot: {out2}")
print(f"bar text: {w._prog.format()}   value={w._prog.value()}/{w._prog.maximum()}")
print(f"stage line: {w._prog_lbl.text()}")
