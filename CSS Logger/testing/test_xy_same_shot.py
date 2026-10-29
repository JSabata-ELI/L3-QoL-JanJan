"""The XY plot pairs X and Y from the SAME shot — never a held value.

Reported 22.10.2026: XY plot of SBW4 vs PCC1 Eff showed SBW4 shots of several
joules at about 0.3 J, and a column of points at X = 0 against full-power
PTM1 readings. Two causes:

- every table row became a point, including rows another channel wrote, with
  X and Y carried forward for minutes — a low reading from one shot paired
  with a high one from another;
- a window over an hour is loaded as 1-minute archive averages, and the XY tab
  said nothing about it.

No network, no archiver — the samples are made up here.

    python testing/test_xy_same_shot.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication      # noqa: E402

import main as css                              # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


PV_X = "TEST:X"            # writes every shot
PV_Y = "TEST:Y"            # writes every 3rd shot, 50 ms late
PV_Z = "TEST:Z"            # writes every shot too (third shot-rate channel)
PV_SLOW = "TEST:SLOW"      # writes halfway between shots
T0 = 1_700_000_000_000_000_000
STEP = 300_000_000         # 3.3 Hz
N = 60


def _samples():
    x = [(T0 + i * STEP, float(i), "") for i in range(N)]
    z = [(T0 + i * STEP + 2_000_000, float(i % 2), "") for i in range(N)]
    y = [(T0 + i * STEP + 50_000_000, 100.0 + i, "") for i in range(0, N, 3)]
    slow = [(T0 + i * STEP + STEP // 2, -5.0, "") for i in range(0, N, 4)]
    return {PV_X: x, PV_Y: y, PV_Z: z, PV_SLOW: slow}


def _rows(sbp):
    w = css.CSSLoggerWidget.__new__(css.CSSLoggerWidget)
    return css.CSSLoggerWidget._build_table_rows(w, sbp, list(sbp))


def _widget(sbp):
    w = css.CSSLoggerWidget()
    w._samples_by_pv = sbp
    w._base_pv_order = list(sbp)
    w._pv_order = list(sbp)
    w._pre_window_vals = {}
    w._table_rows_unfiltered = _rows(sbp)
    w._table_rows = w._apply_conditions_to_rows(w._table_rows_unfiltered)
    labels = {"X ch": PV_X, "Y ch": PV_Y}
    w._xy_choice_map = labels
    for combo, label in ((w._xy_x_combo, "X ch"), (w._xy_y_combo, "Y ch")):
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(list(labels))
        combo.setCurrentText(label)
        combo.blockSignals(False)
    return w


def main():
    app = QApplication.instance() or QApplication([])   # noqa: F841
    sbp = _samples()
    w = _widget(sbp)

    xs, ys, n_shots = w._xy_pairs(PV_X, PV_Y)
    want = [(float(i), 100.0 + i) for i in range(0, N, 3)]
    check("one point per shot in which BOTH wrote", list(zip(xs, ys)) == want,
          f"{len(xs)} points, first {list(zip(xs, ys))[:3]}")
    check("Y archived 50 ms late still pairs with its own shot",
          all(y - x == 100.0 for x, y in zip(xs, ys)))
    check("the shot count is the window's", n_shots == N, f"{n_shots}")
    check("no point from a row another channel wrote",
          len(xs) == len(range(0, N, 3)), f"{len(xs)} points, rows {len(w._table_rows)}")

    # Condition on a third channel: Z == 0 only (even shots).
    w._conditions = [{"pv": PV_Z, "min": -0.5, "max": 0.5}]
    w._table_rows = w._apply_conditions_to_rows(w._table_rows_unfiltered)
    xs, ys, _ = w._xy_pairs(PV_X, PV_Y)
    check("a Condition drops the shots it rejects",
          xs == [float(i) for i in range(0, N, 6)], f"{xs[:5]}")
    w._conditions = []
    w._table_rows = w._apply_conditions_to_rows(w._table_rows_unfiltered)

    # Two channels that never write in the same shot: no points at all.
    xs, ys, _ = w._xy_pairs(PV_X, PV_SLOW)
    check("channels that never share a shot give no points", not xs, f"{len(xs)}")

    # The plot and its info line.
    w._load_thinned = False
    w._live_mode = False
    w._plot_xy()
    info = w._lbl_xy_info.text()
    check("info names the rule", "both channels wrote" in info, repr(info))
    check("raw window: grey line", "#777" in w._lbl_xy_info.styleSheet())
    check("raw window: no averaging warning", "average" not in info, repr(info))

    w._load_thinned = True
    w._plot_xy()
    info = w._lbl_xy_info.text()
    check("thinned window warns about averages", "1-minute" in info, repr(info))
    check("and the warning is in dark ink", "#777" not in w._lbl_xy_info.styleSheet())

    # Only one channel has data.
    w._load_thinned = False
    w._xy_choice_map = {"X ch": PV_X, "Y ch": "TEST:SILENT"}
    w._plot_xy()
    info = w._lbl_xy_info.text()
    check("a silent channel is named as the reason", "one of the channels" in info,
          repr(info))

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
