"""An empty Table and an empty XY plot have to say WHY they are empty.

Reported from the deployed copy: with five Conditions saved and an hour in
which the laser was off, the Table showed "No rows.", the XY tab showed "No
data to plot - neither channel has values in this window." and the Plot XY
button looked dead. Both statements were wrong - every channel had thousands of
samples; it was the Conditions that threw every row away, and only the Log tab
said so.

No network, no archiver - the rows are made up here.

    python testing/test_empty_says_why.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication      # noqa: E402

import main as css                              # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


PV_X = "TEST:X"
PV_Y = "TEST:Y"


def _rows(n, start_ns=1_700_000_000_000_000_000, step_ns=1_000_000_000):
    out = []
    for i in range(n):
        ts = start_ns + i * step_ns
        out.append((ts, {PV_X: (float(i), ts), PV_Y: (float(i) * 2.0, ts)}))
    return out


def _widget():
    w = css.CSSLoggerWidget()
    w._xy_choice_map = {"X ch": PV_X, "Y ch": PV_Y}
    for combo, label in ((w._xy_x_combo, "X ch"), (w._xy_y_combo, "Y ch")):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(["X ch", "Y ch"])
        combo.setCurrentText(label)
        combo.blockSignals(False)
    return w


def main():
    app = QApplication.instance() or QApplication([])
    w = _widget()

    # A condition no row can satisfy — the "laser was off" case.
    w._conditions = [{"pv": PV_X, "min": 1000.0, "max": 2000.0}]
    w._table_rows_unfiltered = _rows(20)
    w._table_rows = w._apply_conditions_to_rows(w._table_rows_unfiltered)
    check("the conditions really discard everything", not w._table_rows,
          f"{len(w._table_rows)} rows left")

    w._populate_table()
    msg = w._lbl_table_info.text()
    check("the table names the Conditions", "Conditions" in msg, repr(msg))
    check("the table says how many were thrown away", "20" in msg, repr(msg))
    check("the table does not claim there was no data",
          "no data" not in msg.lower(), repr(msg))
    check("the table line is dark ink, not grey",
          "#777" not in w._lbl_table_info.styleSheet(),
          w._lbl_table_info.styleSheet())

    w._plot_xy()
    xy = w._lbl_xy_info.text()
    check("the XY tab names the Conditions", "Conditions" in xy, repr(xy))
    check("the XY tab no longer blames the two channels",
          "neither channel" not in xy, repr(xy))
    check("the XY line is dark ink, not grey",
          "#777" not in w._lbl_xy_info.styleSheet(),
          w._lbl_xy_info.styleSheet())

    # Rows present, but the two chosen channels are silent: the old wording is
    # the right one there, and it must come back.
    w._conditions = []
    w._table_rows_unfiltered = [
        (1_700_000_000_000_000_000 + i, {"OTHER:PV": (1.0, i)}) for i in range(5)
    ]
    w._table_rows = w._apply_conditions_to_rows(w._table_rows_unfiltered)
    w._plot_xy()
    xy = w._lbl_xy_info.text()
    check("a silent pair of channels still says so", "neither channel" in xy, repr(xy))

    # And a normal plot puts the line back to grey.
    w._conditions = []
    w._table_rows_unfiltered = _rows(20)
    w._table_rows = w._apply_conditions_to_rows(w._table_rows_unfiltered)
    w._plot_xy()
    check("a real cloud reports its point count",
          "points plotted" in w._lbl_xy_info.text(), repr(w._lbl_xy_info.text()))
    check("and its line is grey again", "#777" in w._lbl_xy_info.styleSheet(),
          w._lbl_xy_info.styleSheet())
    w._populate_table()
    check("the table line is grey again too",
          "#777" in w._lbl_table_info.styleSheet(),
          w._lbl_table_info.styleSheet())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
