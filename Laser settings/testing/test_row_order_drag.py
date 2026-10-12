"""Laser settings — the row order, the Scanning box and renaming in place.

    python testing/test_row_order_drag.py

Asked for on 12.10.2026:

  * rows dragged into the operator's own order on the Limits tab — driven
    through the table's own drop handler (`_RowTable.on_drop` /
    `SettingsTab.move_rows`), never with synthetic mouse input;
  * a formula keeps meaning the same rows after a move;
  * dropping a row under the "Not shown" line unticks its Show;
  * the Overview's Scanning box switches every row with scan limits at once;
  * a name retyped on the Overview reaches the row and the Limits table.

Headless, silent and offline, like `test_edit_in_place.py`.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


def main():
    from PySide6.QtWidgets import QApplication

    import ls_core as C
    import ls_engine

    tmp = tempfile.TemporaryDirectory()
    fake_cfg = Path(tmp.name) / "laser_settings.json"
    C.config_path = lambda: fake_cfg
    ls_engine.PollEngine.start = lambda self: None

    app = QApplication.instance() or QApplication(sys.argv)
    import ls_ui as U
    U.install_app_look(app)

    import main as program
    win = program.LaserSettingsWindow()
    win.resize(1100, 700)

    rows = win.rows()
    rows.clear()
    for i, (pv, name) in enumerate((("L3-X:A", "Alpha"), ("L3-X:B", "Beta"),
                                    ("L3-X:C", "Gamma")), start=1):
        rows.append(C.new_row(i, pv, name))
    calc = C.new_formula_row(4, "Twice A")
    calc["expr"], calc["bind"] = C.expr_from_display("A * 2", rows + [calc])
    rows.append(calc)
    rows.append(C.new_shot_row(9))
    win.settings_changed(rebuild=True)
    ov, lim = win._overview, win._settings

    def flush():
        for _ in range(3):
            app.processEvents()

    def names():
        return [C.row_label(r) for r in win.rows()]

    def overview_names():
        return [w.texts()[0] for w in ov._row_widgets]

    print("a row dragged to the top")
    lim.move_rows([2], 0)                  # Gamma, in front of Alpha
    flush()
    check("the list", names(), ["Gamma", "Alpha", "Beta", "Twice A",
                                "Shot counter"])
    check("the Overview follows", overview_names()[:4],
          ["Gamma", "Alpha", "Beta", "Twice A"])
    check("the formula still means Alpha", calc["bind"], {"A": 1})
    check("and is drawn in today's letters",
          C.expr_to_display(calc, win.rows()), "B * 2")
    check("the shot counter kept its place", C.is_shot_row(win.rows()[-1]),
          True)

    print("a formula dragged above its source still works")
    lim.move_rows([3], 0)                  # Twice A to the top
    flush()
    check("the list", names()[:4], ["Twice A", "Gamma", "Alpha", "Beta"])
    check("no complaint", C.formula_problems(calc, win.rows()), [])

    print("two rows at once, to the bottom of the shown part")
    lim.move_rows([0, 1], 4)
    flush()
    check("the list", names()[:4], ["Alpha", "Beta", "Twice A", "Gamma"])

    print("dropped under the Not shown line, Show goes off")
    beta = win.rows()[1]
    beta["show"] = False
    win.rows_changed()
    flush()
    sep = lim._table_rows.index(None)
    lim.move_rows([0], sep + 1)            # Alpha below the line
    flush()
    alpha = next(r for r in win.rows() if r["id"] == 1)
    check("Alpha unticked", C.is_shown(alpha), False)
    check("and off the Overview", "Alpha" in overview_names(), False)
    sep = lim._table_rows.index(None)
    lim.move_rows([sep + 1], 0)            # the first hidden one back on top
    flush()
    check("dragged back up, shown again",
          C.is_shown(lim._table_rows[0]), True)

    print("the Scanning box on the Overview")
    gamma = next(r for r in win.rows() if r["id"] == 3)
    gamma["show"] = True
    for hall in C.HALLS:
        C.limits_for(gamma, hall).update({"min": 24800.0, "max": 25000.0})
    gamma.update({"scan_min": 24000.0, "scan_max": 26000.0})
    win.rows_changed()
    flush()
    check("off at the start", (win.scanning(), ov._scan_box.isChecked()),
          (False, False))
    ov._scan_box.setChecked(True)
    check("ticked: in the settings", win.scanning(), True)
    check("the Limits table has no Scanning column",
          "Scanning" in [lim._table.horizontalHeaderItem(c).text()
                         for c in range(lim._table.columnCount())], False)
    win.set_scanning(False)
    ov.refresh()
    check("unticked from the window, the box follows",
          ov._scan_box.isChecked(), False)

    print("renamed by double-click on the Overview")
    widget = ov._row_widgets[0]
    target = ov._rows_on_screen[0]
    widget.start_rename()
    widget._edit.setText("Renamed here")
    widget._edit.done.emit(True)
    flush()
    check("into the row", target["name"], "Renamed here")
    check("and the Limits table says so",
          any(lim._table.item(t, 4) is not None
              and lim._table.item(t, 4).text() == "Renamed here"
              for t in range(lim._table.rowCount())), True)
    widget = ov._row_widgets[0]
    widget.start_rename()
    widget._edit.setText("Not kept")
    widget._edit.done.emit(False)
    flush()
    check("Esc keeps the old name", target["name"], "Renamed here")

    for timer in (ov._fit_timer, ov._blink):
        timer.stop()
    win._engine.stop() if hasattr(win._engine, "stop") else None
    print()
    print("all good" if not FAILED else f"{len(FAILED)} FAILED")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
