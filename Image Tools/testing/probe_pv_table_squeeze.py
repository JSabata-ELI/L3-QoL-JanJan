"""Probe: at which window height does the PV list get squeezed, and does collapsing
a section make it worse?

Sweeps the window height with the PV section open and reports, for the timeline
section open and collapsed, how much height the PV table actually got and how many of
its rows a person can read.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/probe_pv_table_squeeze.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_pvsq_"))
os.environ["APPDATA"] = str(_TMP)

import is_t                                                          # noqa: E402
from PySide6.QtWidgets import QApplication, QScrollArea              # noqa: E402

PVS = ["PTM1", "PCM2", "PCM4", "PAP1"]


def rows_readable(tbl) -> int:
    return max(0, tbl.viewport().height() // max(1, tbl.rowHeight(0) or tbl.ROW_H))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    from render_display_rows import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    v = is_t.Viewer()
    v.show()
    v._pv_enabled = list(PVS)
    v._pv_hidden = set()
    v._pv_values = {n: "12.345" for n in PVS}
    v._pv_rebuild_table()
    tbl = v._pv_table
    panel = v.findChild(QScrollArea)

    print(f"table maximumHeight={tbl.maximumHeight()} "
          f"minimumSizeHint={tbl.minimumSizeHint().height()} "
          f"sizeHint={tbl.sizeHint().height()}  ROW_H={tbl.ROW_H}")
    print(f"{'win h':>6} {'panel vp':>9} {'tl open':>18} {'tl shut':>18}")
    for h in (700, 800, 900, 1000, 1040, 1100, 1250):
        out = []
        for expanded in (True, False):
            v.resize(1500, h)
            for key in v._sections:
                v._sections[key].set_expanded(
                    key in ("source", "save", "pv") or (key == "timeline" and expanded))
            for _ in range(3):
                app.processEvents()
            inner = panel.widget()
            out.append(f"{tbl.height():4d}px {rows_readable(tbl)}/{len(PVS)} "
                       f"inner={inner.height():4d}")
        print(f"{h:>6} {panel.viewport().height():>9} {out[0]:>18} {out[1]:>18}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
