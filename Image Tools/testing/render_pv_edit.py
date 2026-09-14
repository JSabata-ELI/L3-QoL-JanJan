"""Render the PV Search window's "Edit PV" dialog, and point a row at another
channel through it.

Offscreen has no fonts and lies about text size, so run with a real platform:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_pv_edit.py

The archiver is stubbed — no network. What to check: the dialog names the channel
the row reads and offers Search beside it; the note under the fields says whether
the name and the unit go to the shared PV list or stay in this window; and after
Search the row on the list reads the newly picked channel.
"""
import os
import sys
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_finder_moment import load_finder, prague_bounds

DAY = date(2026, 8, 17)
FAKE_CHANNELS = [
    "C03-040-PTM11WNF-_-IMG:TotalPower",
    "L3-Laser-SBW4:Energy",
    "L3-Laser-SBW4b:Energy",
    "L3-Compressor-Waveplate:Angle",
    "L3-Pulser-HighPowerEnable",
]


def main() -> int:
    m = load_finder()
    from PySide6.QtCore import QDate, QTimer
    from PySide6.QtWidgets import QApplication, QDialog
    from pathlib import Path

    t8 = prague_bounds(DAY, 8)
    series = [(t8 + i * 10 * 1_000_000_000, 9.0 + (i % 60) / 60.0)
              for i in range(600)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))
    # The channel listing, without asking the archiver for its ~9700 names.
    m._PV_CHANNEL_CACHE = list(FAKE_CHANNELS)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    from render_save_view_dialog import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    dlg = m.PVRegionSearchDialog(cams, [QDate(DAY.year, DAY.month, DAY.day)])
    dlg.show()

    out_dir = Path(__file__).parent
    shots: list = []

    def shoot():
        dlg._pv_list.setCurrentRow(3)          # PAP1
        row = dlg._pv_list.currentItem()
        print("row before:", row.text(), "→", row.data(0x0100))

        # The Edit dialog, caught while it is open.
        def grab_edit():
            for w in app.topLevelWidgets():
                if isinstance(w, QDialog) and w.windowTitle() == "Edit PV":
                    w.grab().save(str(out_dir / "pv_edit.png"))
                    shots.append("pv_edit.png")
                    # Now the Search half: open it, pick a channel, accept.
                    def grab_browse():
                        for b in app.topLevelWidgets():
                            if isinstance(b, QDialog) and \
                                    b.windowTitle() == "Read which channel":
                                b._filter.setText("waveplate")
                                app.processEvents()
                                b._list.setCurrentRow(0)
                                b.grab().save(str(out_dir / "pv_edit_search.png"))
                                shots.append("pv_edit_search.png")
                                b.accept()
                                return
                    from PySide6.QtWidgets import QPushButton
                    btn = next(b for b in w.findChildren(QPushButton)
                               if b.text() == "Search")
                    QTimer.singleShot(400, grab_browse)
                    QTimer.singleShot(200, btn.click)
                    QTimer.singleShot(1200, lambda: (
                        w.grab().save(str(out_dir / "pv_edit_after.png")),
                        shots.append("pv_edit_after.png"),
                        w.accept()))
                    return
        QTimer.singleShot(600, grab_edit)
        dlg._edit_selected_pv()            # blocks on its own event loop
        done()

    def done():
        row = dlg._pv_list.item(3)
        print("row after :", row.text(), "→", row.data(0x0100))
        print("status:", dlg._status.text())
        print("written:", ", ".join(shots))
        app.quit()

    QTimer.singleShot(1500, shoot)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
