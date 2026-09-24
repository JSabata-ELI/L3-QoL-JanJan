"""LOOK at the PV list when the readings come from another moment.

The new "(-2.5 s)" label rides in the Value column, which sizes to its contents inside
a 275 px panel — so the question a test cannot answer is whether the PV names still fit
beside it. Writes pv_offset_rows.png and prints each column's width and whether any
text is elided.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/render_pv_offset_row.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_pvoff_"))
os.environ["APPDATA"] = str(_TMP)

import is_t                                                          # noqa: E402
from PySide6.QtWidgets import QApplication                           # noqa: E402

PVS = ["PTM1", "PCM2", "PCM4", "Back_Ref"]


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    from render_display_rows import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    v = is_t.Viewer()
    v.resize(1500, 950)
    v.show()
    app.processEvents()

    frame = int(time.time() * 1e9)
    v.items = [type("It", (), {"ts_ns": frame})()]
    v.ts_list = [frame]
    v.current_idx = 0
    v._pv_enabled = list(PVS)
    v._pv_hidden = set()
    v._pv_values = {"PTM1": "90.412", "PCM2": "12.954",
                    "PCM4": "8.031", "Back_Ref": "0.008"}
    # Three readings from other moments, one of this frame's own.
    v._pv_sample_ts = {"PTM1": frame - 2_500_000_000,
                       "PCM2": frame + 4_100_000_000,
                       "PCM4": frame - 47_000_000_000,
                       "Back_Ref": frame}
    v._pv_rebuild_table()
    app.processEvents()

    tbl = v._pv_table
    print("rows as the operator reads them")
    for r in range(tbl.rowCount()):
        name = tbl.item(r, 1).text()
        val = tbl.item(r, 2).text()
        print(f"  {name:12s} {val}")
    print(f"columns: eye {tbl.columnWidth(0)} px, PV {tbl.columnWidth(1)} px, "
          f"value {tbl.columnWidth(2)} px  (table {tbl.width()} px)")
    fm = tbl.fontMetrics()
    for r in range(tbl.rowCount()):
        for c in (1, 2):
            it = tbl.item(r, c)
            need = fm.horizontalAdvance(it.text()) + 8
            if need > tbl.columnWidth(c):
                print(f"  CLIPPED r{r}c{c}: {it.text()!r} needs {need} px, "
                      f"has {tbl.columnWidth(c)}")
    out = Path(__file__).resolve().parent / "pv_offset_rows.png"
    v._sections["pv"].grab().save(str(out))
    print(f"  wrote {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
