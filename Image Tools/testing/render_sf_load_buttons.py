"""Render Shot Finder's two load buttons and a table of "data only" rows.

Offscreen has no fonts and lies about text size, so this needs a real platform:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_sf_load_buttons.py

What to look at in the two pictures it writes:
  * `sf_load_buttons.png` — Load data and Load images side by side in the 275 px
    panel. Both labels whole, dark ink on the pale button / white on the blue one,
    neither one cut off.
  * `sf_data_rows.png` — a table filled by Load data: green rows saying
    "no image loaded", the Folder cell a grey dash, and the preview pane on the
    right carrying a readable line instead of standing empty.
  * `sf_img_column.png` — the SAME rows after Load images has answered two of
    them. The Image column carries a green ✓ where the frame is there, a red ✕
    where it was looked for and was not, and the grey dash on the row still
    waiting. All three legible, centred, and none of them taking the row's own
    colour.

Nothing is read from the archiver or the share.
"""
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtWidgets import QApplication            # noqa: E402

import sf_t                                            # noqa: E402

DAYS = [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]
CAMS = ["C02-101-CAM", "C02-102-CAM"]
COL = "sbw4"


def data_result(day, cam):
    tz = sf_t.PRAGUE if sf_t.PRAGUE else timezone.utc
    rows = []
    for i in range(6):
        dt = datetime(day.year, day.month, day.day, 8 + i, 0, tzinfo=tz)
        rows.append({"_dt": dt.replace(tzinfo=None),
                     "_ns": int(dt.timestamp() * 1e9),
                     COL: f"{10.0 + i * 0.01:.3f}"})
    return {
        "day": day, "cam": cam, "status": "data", "reason": "",
        "best_row": rows[0], "rows_in_tol": rows, "col": COL,
        "actual": 10.0, "diff": 0.0, "target_csv": 10.0,
        "hour_folder": None, "folder_path": None, "img_path": None,
        "per_col": {COL: rows}, "col_meta": {COL: {"source": "api", "status": "ok"}},
        "search_cols": [COL], "extra_cols": [],
        "criteria_csv": [{"col": COL, "target_csv": 10.0, "target_ui": 10.0,
                          "tol_ui": 1.0, "tol_csv": 1.0}],
        "display_vals": {COL: (f"{10.0:.3f}", "ok")},
    }


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                      "QLabel { background: transparent; }")

    w = sf_t.ShotFinderWidget()
    w.resize(1400, 860)
    w.show()
    app.processEvents()

    w._run_wants_images = False
    w._rebuild_result_tabs(CAMS)
    for day in DAYS:
        for cam in CAMS:
            w._on_day_result(data_result(day, cam))
    w._on_search_done()
    w._table.selectRow(0)
    app.processEvents()

    out1 = HERE / "sf_load_buttons.png"
    # The two buttons with a little of the panel around them.
    r = w._btn_search.geometry().united(w._btn_load_images.geometry())
    parent = w._btn_search.parentWidget()
    parent.grab(r.adjusted(-8, -8, 8, 8)).save(str(out1))
    print("wrote", out1)
    print("  left :", w._btn_search.text(), w._btn_search.size())
    print("  right:", w._btn_load_images.text(), w._btn_load_images.size())

    out2 = HERE / "sf_data_rows.png"
    w.grab().save(str(out2))
    print("wrote", out2)
    print("  heading:", w._result_lbl.text())
    print("  status :", w._table.item(0, sf_t.RES_COL_STATUS).text())
    print("  image  :", w._table.item(0, sf_t.RES_COL_IMG).text())
    print("  folder :", w._table.item(0, sf_t.RES_COL_FOLDER).text())

    # Load images answering two of those rows in place — the third is left waiting,
    # so all three states of the Image column are in one picture.
    w._run_wants_images = True
    for cam in CAMS:
        found = data_result(DAYS[0], cam)
        found.update({"status": "ok", "reason": "",
                      "hour_folder": Path(rf"\\share\images\{DAYS[0]}\08"),
                      "folder_path": Path(rf"\\share\images\{DAYS[0]}\08\{cam}"),
                      "img_path": Path(rf"\\share\images\{DAYS[0]}\08\{cam}\f.png")})
        w._on_day_result(found)
        missing = data_result(DAYS[2], cam)
        missing.update({"status": "no_image",
                        "reason": "this camera was not recording then",
                        "hour_folder": Path(rf"\\share\images\{DAYS[2]}\08"),
                        "folder_path": None, "img_path": None})
        w._on_day_result(missing)
    w._on_search_done()
    app.processEvents()

    out3 = HERE / "sf_img_column.png"
    w.grab().save(str(out3))
    print("wrote", out3)
    for r in range(w._table.rowCount()):
        print(f"  row {r}: {w._table.item(r, sf_t.RES_COL_DATE).text()}"
              f"  image={w._table.item(r, sf_t.RES_COL_IMG).text()}"
              f"  ink={w._table.item(r, sf_t.RES_COL_IMG).foreground().color().name()}"
              f"  status={w._table.item(r, sf_t.RES_COL_STATUS).text()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
