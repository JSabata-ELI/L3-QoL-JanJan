"""Laser settings — an edit changes what it changes, and nothing else.

    python testing/test_edit_in_place.py

Pins four promises made on 6.10.2026 ("při změně parametrů nic nerefreshovat,
ale hned upravit"):

  * a box on the Settings tab, a drag on its board, and the counter line's
    size repaint the Overview's EXISTING rows — the row widgets are the same
    objects afterwards, and the Limits table was not refilled;
  * a hall button on the Limits tab rewrites Min / Max in place — same items;
  * Show still adds and removes a row on the Overview;
  * a number box or drop-down nobody clicked never answers the wheel: Qt's
    WheelFocus is taken off every one of them (Qt hands such a widget the
    focus on a wheel notch before any filter sees it), and an unfocused box
    passes the notch on.

Headless, silent and offline, like `test_window_builds.py`.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
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
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox)

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
    for i, (pv, name, unit) in enumerate((
            ("L3-X:Delay", "Front end delay", "ns"),
            ("L3-X:Volt", "Amplifier 1 voltage", "V"),
            ("L3-X:GDD", "GDD", "fs^2")), start=1):
        rows.append(C.new_row(i, pv, name, unit))
    rows.append(C.new_shot_row(9))
    win.settings_changed(rebuild=True)
    ov, lim, ft = win._overview, win._settings, win._font

    def row_ids():
        return [id(w) for w in ov._row_widgets]

    def table_items():
        return [id(lim._table.item(r, 0)) for r in range(lim._table.rowCount())]

    print("a Settings box repaints the rows it has")
    before_rows, before_items = row_ids(), table_items()
    ft._size.setValue(ft._size.value() + 3)
    check("the same row widgets", row_ids(), before_rows)
    check("the Limits table was not refilled", table_items(), before_items)
    check("and the rows wear the new size",
          ov._row_widgets[0]._look_key[1], int(win.look()["size_px"]))
    ft._bold.setChecked(not ft._bold.isChecked())
    check("Bold: the same row widgets", row_ids(), before_rows)
    ft._single.setChecked(True)
    check("one square: the same row widgets", row_ids(), before_rows)
    ft._single.setChecked(False)
    ft._fit.setChecked(True)
    check("fit back on: the same row widgets", row_ids(), before_rows)

    print("a drag on the board moves the squares, nothing is rebuilt")
    t0 = time.perf_counter()
    for _ in range(20):
        ft._board.changed.emit()
    per_move = (time.perf_counter() - t0) / 20 * 1000
    check("the same row widgets", row_ids(), before_rows)
    check("the Limits table was not refilled", table_items(), before_items)
    print(f"        ({per_move:.1f} ms per mouse move)")

    print("the counter line's size repaints in place")
    win.shot_settings()["size_px"] = 22
    win.shot_look_changed()
    check("the same row widgets", row_ids(), before_rows)

    print("a hall button rewrites Min / Max in place")
    rows[0]["limits"] = {"E2": {"min": 1.0, "max": 2.0},
                         "E3": {"min": 5.0, "max": 6.0}}
    import st_t
    col_min = st_t._C_MIN
    lim._pick_hall("E2")
    min_item = lim._table.item(0, col_min)
    lim._pick_hall("E3")
    check("the same items", table_items(), before_items)
    check("the same Min cell", lim._table.item(0, col_min) is min_item, True)
    check("showing E3's Min", lim._table.item(0, col_min).text(),
          C.level_text(5.0))
    lim._pick_hall("E2")
    check("and E2's again", lim._table.item(0, col_min).text(),
          C.level_text(1.0))

    print("Show still takes a row off the Overview and puts it back")
    rows[1]["show"] = False
    win.rows_changed()
    check("two rows on the Overview", len(ov._row_widgets), 2)
    rows[1]["show"] = True
    win.rows_changed()
    check("three again", len(ov._row_widgets), 3)

    print("no box answers the wheel before it was clicked")
    guarded = [w for w in ft.findChildren(QAbstractSpinBox)
               + ft.findChildren(QComboBox)]
    for w in guarded:
        w.ensurePolished()
    check("there are boxes to check", len(guarded) >= 9, True)
    check("none is WheelFocus",
          [type(w).__name__ for w in guarded
           if w.focusPolicy() == Qt.FocusPolicy.WheelFocus], [])
    box = ft._mark
    was = box.value()
    ev = QWheelEvent(QPointF(5, 5), QPointF(box.mapToGlobal(QPoint(5, 5))),
                     QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase,
                     False)
    QApplication.sendEvent(box, ev)
    check("an unclicked box keeps its value", box.value(), was)

    print("the Maximise boxes re-maximise once, after the edits settle")
    calls = []
    win.isMaximized = lambda: True
    win._remaximise = lambda: calls.append(1)
    win._remax_timer.timeout.disconnect()
    win._remax_timer.timeout.connect(win._remaximise)
    for _ in range(5):
        win.window_max_changed()
    check("nothing on each step", calls, [])
    check("one waiting", win._remax_timer.isActive(), True)

    for timer in (getattr(ov, "_fit_timer", None),
                  getattr(ov, "_blink", None), win._remax_timer):
        if timer is not None:
            timer.stop()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
