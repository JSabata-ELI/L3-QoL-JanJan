"""Laser settings — the DM settings and DM writer tabs, end to end, and pictures.

    python testing/test_dm_tabs.py

The whole round trip without OPR1 and without the share: the two screenshots
stand in for the Remote Desktop window, a temporary folder stands in for the
scratch share, and the engine's dm thread is replaced by a stand-in that runs
`ls_dm.exchange` straight away. Then the tabs are drawn into PNG files next to
this script, with `WA_DontShowOnScreen` — on the real `windows` platform for
real fonts, and nothing ever reaches the screen or takes the keyboard.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from PySide6.QtCore import Qt                                   # noqa: E402
from PySide6.QtWidgets import QApplication                      # noqa: E402

import ls_core as C                                             # noqa: E402
import ls_dm as D                                               # noqa: E402
import ls_ui as U                                               # noqa: E402
from test_dm_grid import picture, screen                        # noqa: E402

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


class FakeEngine:
    """The dm half of `PollEngine`, run on the spot instead of on a thread."""

    def __init__(self, share):
        self.share = share
        self.local = None
        self._dm = None

    def dm(self):
        return self._dm

    def look(self, local=None, edits=()):
        self.local = local
        self._dm = D.exchange(self.share, local, list(edits))

    def dm_edit(self, edit):
        self.look(self.local, [edit])


class FakeWindow:
    def __init__(self, engine):
        self._engine = engine
        self._cfg = C.default_config()

    def engine(self):
        return self._engine

    def dm_settings(self):
        return C.dm_of(self._cfg)

    def dm_changed(self):
        pass


def grab(widget, name, size=(1100, 900)):
    widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    widget.resize(*size)
    widget.show()                       # never reaches the screen
    QApplication.processEvents()
    QApplication.processEvents()
    widget.grab().save(str(HERE / name), "PNG")
    widget.hide()
    print(f"        wrote {name}")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    U.install_app_look(app)
    from dm_t import DmTab
    from dw_t import DmWriterTab

    tmp = tempfile.TemporaryDirectory()
    share = D.Share(roots=[tmp.name])
    engine = FakeEngine(share)
    win = FakeWindow(engine)
    tab = DmTab(win)
    writer = DmWriterTab(win)

    low = D.read_picture(picture(screen("dm_0731.png")))
    high = D.read_picture(picture(screen("dm_0727.png")))

    print("nothing written yet")
    engine.look()
    tab.refresh()
    check("says so", tab._banner.text(), "Not read yet")
    check("Save is off without a reading", tab._save_btn.isEnabled(), False)
    grab(tab, "dm_tab_nothing.png")

    print("OPR1 reads the 7:31 grid: Low power, built in, nothing saved")
    engine.look(low)
    tab.refresh()
    check("the state file is on the share",
          (Path(tmp.name) / D.SHARE_SUBDIR / D.STATE_NAME).exists(), True)
    check("recognised", tab._banner.text(), "Low power")
    check("both built-in ones are in the list", tab._names,
          ["Low power", "High power"])
    check("Save is on", tab._save_btn.isEnabled(), True)

    print("a grid that is neither: saved under a name of its own")
    odd = [list(c) for c in low.colours]
    odd[7] = [255, 0, 0]
    engine.look(low._replace(colours=odd))
    tab.refresh()
    check("not a saved set-up", tab._banner.text(), "Not a saved set-up")
    grab(tab, "dm_tab_unsaved.png")
    tab._name.setText("Mine")
    tab._save()
    tab.refresh()
    check("saved", tab._note.text(), "Saved \"Mine\".")
    check("and recognised", tab._banner.text(), "Mine")
    check("the file holds only it", sorted(D.clean_setups(D.read_json(
        Path(tmp.name) / D.SHARE_SUBDIR / D.SETUPS_NAME))), ["Mine"])

    print("OPR1 reads the 7:27 grid")
    engine.look(high)
    tab.refresh()
    check("High power", tab._banner.text(), "High power")
    check("all three in the list", tab._names,
          ["Low power", "High power", "Mine"])
    grab(tab, "dm_tab_match.png")

    print("the High power / Low power ticks")
    import dm_t
    ticks = lambda: [(tab._table.item(r, dm_t._C_HIGH).checkState()     # noqa
                      == Qt.CheckState.Checked,
                      tab._table.item(r, dm_t._C_LOW).checkState()
                      == Qt.CheckState.Checked)
                     for r in range(tab._table.rowCount())]
    check("built-in ones ticked as named, Mine for neither", ticks(),
          [(False, True), (True, False), (False, False)])
    tab._table.item(2, dm_t._C_HIGH).setCheckState(Qt.CheckState.Checked)
    tab.refresh()
    check("Mine ticked High power, on the share",
          D.tag_of("Mine", D.clean_setups(D.read_json(
              Path(tmp.name) / D.SHARE_SUBDIR / D.SETUPS_NAME))), D.TAG_HIGH)
    tab._table.item(2, dm_t._C_LOW).setCheckState(Qt.CheckState.Checked)
    tab.refresh()
    check("Low power unticks High power", ticks()[2], (False, True))
    tab._table.item(2, dm_t._C_HIGH).setCheckState(Qt.CheckState.Checked)
    tab._table.item(1, dm_t._C_HIGH).setCheckState(Qt.CheckState.Unchecked)
    tab.refresh()
    check("built-in High power unticked, Mine back on High", ticks(),
          [(False, True), (False, False), (True, False)])
    grab(tab, "dm_tab_ticks.png")
    tab._table.item(1, dm_t._C_HIGH).setCheckState(Qt.CheckState.Checked)
    tab.refresh()
    tab._table.clearSelection()

    print("Low power picked in the list: the electrodes that differ are marked")
    tab._table.selectRow(0)
    tab.refresh()
    check("the right grid is Low power", tab._saved.caption(),
          "Saved: Low power")
    # 21 differ by more than 10 levels, 18 by more than MATCH_TOL (15).
    check("18 electrodes marked", len(tab._now._marked), 18)
    grab(tab, "dm_tab_compare.png")
    tab._table.clearSelection()

    print("saving the same colours again under a new name is refused")
    tab._name.setText("Copy")
    tab._save()
    tab.refresh()
    check("refused", tab._note.text(),
          "These colours are already saved, as \"High power\".")

    print("the window minimised on OPR1: Unknown, and no colours")
    engine.look(D.DmRead(D.STATUS_MINIMISED, None, high.at,
                         problem=D.STATUS_TEXT[D.STATUS_MINIMISED]))
    tab.refresh()
    check("unknown", tab._banner.text(), "Unknown")
    check("no colours drawn", tab._now._colours, None)
    check("and it says why", "minimised" in tab._detail.text(), True)
    grab(tab, "dm_tab_minimised.png")

    print("a built-in set-up cannot be deleted, but can be renamed")
    engine.look(high)
    tab.refresh()
    tab._table.selectRow(0)
    tab.refresh()
    check("Delete off", tab._delete_btn.isEnabled(), False)
    check("Rename on", tab._rename_btn.isEnabled(), True)
    tab._table.clearSelection()

    print("Delete wants two clicks")
    tab._table.selectRow(2)
    tab._delete()
    check("first click arms it", tab._delete_btn.text(),
          "Click again to delete 1")
    tab._delete()
    tab.refresh()
    check("second click deletes", tab._names, ["Low power", "High power"])

    print("the share cannot be reached")
    dead = FakeEngine(D.Share(roots=[str(Path(tmp.name) / "nowhere")]))
    dead_tab = DmTab(FakeWindow(dead))
    dead.look(high)
    dead_tab.refresh()
    check("it says so", dead_tab._problem.isHidden(), False)

    print("the DM writer tab")
    writer.refresh()
    check("off on a fresh install", writer._status.text().startswith("Off"),
          True)
    grab(writer, "dm_writer_off.png", size=(1000, 600))
    writer._write.setChecked(True)
    writer.refresh()
    check("on, showing the last look", writer._status.text().startswith(
        "Last look at"), True)
    grab(writer, "dm_writer_on.png", size=(1000, 600))

    for w in (tab, writer, dead_tab):
        w.deleteLater()
    QApplication.processEvents()
    tmp.cleanup()

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for line in FAILED:
            print("  " + line)
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
