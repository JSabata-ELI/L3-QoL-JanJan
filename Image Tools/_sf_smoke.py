"""Smoke test: build ShotFinderWidget, add a 2nd search PV + an also-show PV,
render a fake multi-PV result row, screenshot. No network needed."""
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

import sf_t

app = QApplication(sys.argv)
w = sf_t.ShotFinderWidget()
w.resize(1400, 800)
w.show()

# Issue 6: add a second search PV and an also-show PV via the new API
w._add_criteria_col("ptm1")
w._add_extra_col("Back_Ref")
# also exercise a free-typed arbitrary channel
w._add_extra_col("L3-SBDP-SPIDER:SpecDomain_Int_X")

# Build a fake day result to exercise issue-4 multi-line rendering
dt = datetime(2026, 6, 10, 14, 30, 15, 123000)
import time
ns = 1_717_000_000_000_000_000
best_row = {"_dt": dt, "_ns": ns, "sbw4": "13.0", "ptm1": "5.0",
            "Back_Ref": "0.012", "L3-SBDP-SPIDER:SpecDomain_Int_X": "42.5"}
per_col = {k: [best_row] for k in
           ["sbw4", "ptm1", "Back_Ref", "L3-SBDP-SPIDER:SpecDomain_Int_X"]}
result = {
    "day": dt.date(),
    "best_row": best_row,
    "rows_in_tol": [best_row, best_row],
    "col": "sbw4",
    "actual": 13.0,
    "diff": 0.0,
    "target_csv": 13.0,
    "hour_folder": Path.home(),
    "cam": "",
    "extra_cols": ["Back_Ref", "L3-SBDP-SPIDER:SpecDomain_Int_X"],
    "search_cols": ["sbw4", "ptm1"],
    "per_col": per_col,
    "criteria_csv": [
        {"col": "sbw4", "target_csv": 13.0, "tol_csv": 0.2,
         "target_ui": 9.737, "tol_ui": 0.2},
        {"col": "ptm1", "target_csv": 5.5, "tol_csv": 0.1,
         "target_ui": 5.5, "tol_ui": 0.1},   # value 5.0 → 0.5 off → ⚠
    ],
}
w._on_day_result(result)

def shoot():
    pix = w.grab()
    out = Path(__file__).with_name("_sf_smoke.png")
    pix.save(str(out))
    print("SAVED", out, "rows", w._table.rowCount(),
          "criteria_cols", w._criteria_cols, "extra", w._extra_cols_sel)
    app.quit()

QTimer.singleShot(800, shoot)
sys.exit(app.exec())
