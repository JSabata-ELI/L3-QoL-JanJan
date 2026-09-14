"""Two per-PV settings under the PV Search list: "Own axis" and "Edit".

OWN AXIS BELONGS TO ONE PV. Three PVs in joules share one value axis; putting the
middle one on an axis of its own must move that one and leave the other two where
they were. The state is kept per channel, the row that carries it is marked on the
list (an amber band, so it is not only the state of a button that changes as the
selection moves), and the button follows whichever row is selected.

EDIT CHANGES WHICH CHANNEL THE ROW READS. A preset row used to be welded to the
channel it was seeded with, so "PAP1" could never be pointed anywhere else. Now the
row's channel is part of Edit: it is searched with the same archiver-wide PV search
the rest of the program uses, and everything the window kept against the old
channel — its colour, its unit, its own-axis flag — moves with it or goes.

    python testing/test_pv_own_axis_and_edit.py
"""
import sys
from datetime import date
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder, prague_bounds
from test_pv_graph import (ARCHIVE, DAY, ENERGY_CH, ENERGY2_CH, STEP_CH,
                           curves, install_stubs, make_dialog, restore_stubs)

FAILURES: "list[str]" = []
PCM2_CH = "L3-PM03-025:Energy"              # registry: PCM2, unit J
NEW_CH = "L3-Compressor-Waveplate:Angle"    # not on the shared PV list


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def select(dlg, channel: str):
    from PySide6.QtCore import Qt
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == channel:
            dlg._pv_list.setCurrentItem(it)
            return it
    return None


def row_of(dlg, channel: str):
    from PySide6.QtCore import Qt
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == channel:
            return it
    return None


def check_own_axis(m):
    print("=== Own axis belongs to ONE PV ===")
    base = prague_bounds(DAY, 10)
    a = [(base + i * 60_000_000_000, 5.0 + i * 0.5) for i in range(12)]
    b = [(base + i * 60_000_000_000, 120.0 + i) for i in range(12)]
    c = [(base + i * 60_000_000_000, 900.0 + i * 3) for i in range(12)]
    dlg = make_dialog(m, {ENERGY_CH: a, ENERGY2_CH: b, PCM2_CH: c},
                      checked=(ENERGY_CH, ENERGY2_CH, PCM2_CH))
    check("three PVs in joules share one axis",
          len(dlg._axes_extra) == 0, f"{1 + len(dlg._axes_extra)} axes")
    check("and all three are drawn", len(curves(dlg)) == 3, str(list(curves(dlg))))

    select(dlg, ENERGY2_CH)
    dlg._toggle_own_axis(True)
    check("one of them on its own axis makes exactly ONE more axis",
          len(dlg._axes_extra) == 1, f"{1 + len(dlg._axes_extra)} axes")
    check("and only that PV is on it",
          dlg._own_axis == {ENERGY2_CH}, str(sorted(dlg._own_axis)))
    on_extra = [lbl for lbl, (_ln, ax) in curves(dlg).items()
                if ax is not dlg._ax]
    check("the other two stayed on the shared axis",
          len(on_extra) == 1, f"on the extra axis: {on_extra}")

    it = row_of(dlg, ENERGY2_CH)
    check("the row that carries it is marked on the list",
          it.background().color().name().lower() == "#ffeec2",
          it.background().color().name())
    check("and says so when the mouse rests on it",
          "axis of its own" in it.toolTip(), repr(it.toolTip()[-40:]))
    other = row_of(dlg, ENERGY_CH)
    check("a PV that is NOT on its own axis is not marked",
          other.background().color().name().lower() == "#ffffff",
          other.background().color().name())

    # The button follows the selection — it is not a switch for the whole graph.
    select(dlg, ENERGY_CH)
    dlg._sync_pv_buttons()
    check("selecting another PV shows the button unpressed",
          not dlg._btn_own_axis.isChecked())
    select(dlg, ENERGY2_CH)
    dlg._sync_pv_buttons()
    check("and going back to that PV shows it pressed",
          dlg._btn_own_axis.isChecked())

    dlg._toggle_own_axis(False)
    check("taking it off puts the PV back on the shared axis",
          len(dlg._axes_extra) == 0 and not dlg._own_axis,
          f"{1 + len(dlg._axes_extra)} axes, {sorted(dlg._own_axis)}")
    check("and the row is no longer marked",
          row_of(dlg, ENERGY2_CH).background().color().name().lower() == "#ffffff")

    # Nothing selected: the setting has nothing to apply to and says so, rather
    # than quietly doing it to every PV.
    dlg._pv_list.setCurrentItem(None)
    dlg._toggle_own_axis(True)
    check("with no PV selected nothing goes on its own axis",
          not dlg._own_axis, str(sorted(dlg._own_axis)))
    check("and it says to click a PV first",
          "Click a PV" in dlg._status.text(), dlg._status.text())
    dlg.close()


def check_edit_channel(m):
    print("\n=== Edit changes WHICH channel a row reads ===")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (QApplication, QDialog, QLineEdit,
                                   QPushButton, QDialogButtonBox)
    base = prague_bounds(DAY, 10)
    a = [(base + i * 60_000_000_000, 5.0 + i * 0.5) for i in range(12)]
    fresh = [(base + i * 60_000_000_000, 30.0 + i) for i in range(12)]
    dlg = make_dialog(m, {ENERGY_CH: a, NEW_CH: fresh}, checked=(ENERGY_CH,))
    it = select(dlg, ENERGY_CH)
    dlg._own_axis.add(ENERGY_CH)
    dlg._refresh_own_axis_marks()
    old_colour = dlg._colour_for(ENERGY_CH)

    # Drive the dialog without touching the screen: it is modal, so the work is
    # done from a timer inside its own event loop.
    from PySide6.QtCore import QTimer
    m._PV_CHANNEL_CACHE = [ENERGY_CH, NEW_CH, "L3-Pulser-HighPowerEnable"]
    seen: dict = {}

    def drive():
        ed = next((w for w in QApplication.instance().topLevelWidgets()
                   if isinstance(w, QDialog) and w.windowTitle() == "Edit PV"), None)
        if ed is None:
            return
        seen["channel_shown"] = next(
            (e.text() for e in ed.findChildren(QLineEdit) if e.isReadOnly()), "")
        btn = next(b for b in ed.findChildren(QPushButton) if b.text() == "Search")

        def pick():
            br = next((w for w in QApplication.instance().topLevelWidgets()
                       if isinstance(w, QDialog)
                       and w.windowTitle() == "Read which channel"), None)
            if br is None:
                seen["browse"] = False
                return
            seen["browse"] = True
            br._filter.setText("waveplate angle")
            seen["ranked"] = [br._list.item(i).text()
                              for i in range(br._list.count())]
            br._list.setCurrentRow(0)
            br.accept()

        QTimer.singleShot(0, btn.click)
        QTimer.singleShot(50, pick)
        QTimer.singleShot(150, lambda: next(
            b for b in ed.findChildren(QDialogButtonBox)).button(
                QDialogButtonBox.StandardButton.Ok).click())

    QTimer.singleShot(0, drive)
    dlg._edit_selected_pv()

    check("the dialog named the channel the row was reading",
          seen.get("channel_shown") == ENERGY_CH, repr(seen.get("channel_shown")))
    check("Search offers the archiver-wide PV search", seen.get("browse") is True)
    check("and it ranks by the words typed, not the alphabet",
          seen.get("ranked", [None])[0] == NEW_CH, repr(seen.get("ranked")))
    check("the row now reads the picked channel",
          it.data(Qt.ItemDataRole.UserRole) == NEW_CH,
          str(it.data(Qt.ItemDataRole.UserRole)))
    check("the own-axis flag moved with the row, not with the old channel",
          dlg._own_axis == {NEW_CH}, str(sorted(dlg._own_axis)))
    check("the old channel's colour was not left on the new PV",
          dlg._colour_for(NEW_CH) != old_colour or ENERGY_CH not in dlg._pv_colour,
          f"{old_colour} → {dlg._colour_for(NEW_CH)}")
    check("and the status line says what the row reads now",
          NEW_CH in dlg._status_note or NEW_CH in dlg._status.text(),
          dlg._status.text())

    B.wait_for(lambda: NEW_CH in (dlg._series.get(DAY) or {}), timeout_s=10.0)
    dlg._redraw()
    check("the new channel's samples were fetched and drawn",
          any(len(ln.get_xdata()) for ln, _ax in curves(dlg).values()),
          str(list(curves(dlg))))
    dlg.close()


def main():
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    orig = install_stubs(m)
    try:
        check_own_axis(m)
        check_edit_channel(m)
    finally:
        restore_stubs(m, orig)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
