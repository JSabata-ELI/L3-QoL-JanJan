"""Render the PV Search window so it can be LOOKED at.

Offscreen has no fonts and lies about text size, so run this with a real platform:

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python render_pv_search.py

It stubs the archiver (no network), fills a day with samples, picks three moments
and marks three regions, then writes `pv_search.png` beside this file. What to
check: the PV list is white with dark ink and its added row is visible; the day
list under the calendar names the day, opens, and lists every moment and region on
it; the Search button sits DIRECTLY UNDER that list and counts the picks; every
pick — a moment and a region alike — is drawn on the graph with its own number,
the regions one badge lower so the two never print over each other; and the one
table under the graph carries the picks on the left and their statistics on the
right, a region's description merged down its PV rows with the ✕ centred on it.
"""
import os
import sys
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from test_finder_moment import load_finder, prague_bounds

DAY = date(2026, 8, 17)


def main() -> int:
    m = load_finder()
    from PySide6.QtCore import QDate, QTimer
    from PySide6.QtWidgets import QApplication
    from pathlib import Path

    t8 = prague_bounds(DAY, 8)
    # A day of shots, one every 10 s from 08:00 on, with a slow drift so the curve
    # has a shape to look at.
    series = [(t8 + i * 10 * 1_000_000_000, 9.0 + 1.5 * ((i % 360) / 360.0))
              for i in range(3600)]
    m.PVRegionSearchDialog._fetch_window = staticmethod(
        lambda channel, start_ns, end_ns: (
            [(t, v) for (t, v) in series if start_ns <= t <= end_ns], "ok", ""))

    app = QApplication.instance() or QApplication(sys.argv)
    # Under the program's OWN stylesheet, or the panel is judged on colours it never
    # actually has.
    app.setStyle("Fusion")
    from render_save_view_dialog import app_stylesheet
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    dlg = m.PVRegionSearchDialog(cams, [QDate(DAY.year, DAY.month, DAY.day)])
    dlg.show()

    def shoot():
        # Five PVs plotted, so the graph and the range table are seen under the
        # load they are actually complained about at.
        from PySide6.QtCore import Qt
        n_on = 0
        for i in range(dlg._pv_list.count()):
            it = dlg._pv_list.item(i)
            if n_on < 5:
                it.setCheckState(Qt.CheckState.Checked)
                n_on += 1
        # One of them on a value axis of its own — the amber row.
        dlg._pv_list.setCurrentRow(1)
        dlg._btn_own_axis.setChecked(True)
        dlg._toggle_own_axis(True)
        # …and the selection moved off it, so the amber band is judged on its own
        # and not under the selected row's blue.
        dlg._pv_list.setCurrentRow(0)
        # Three picks and THREE marked regions, through the real gestures.
        for i in (400, 1200, 2600):
            dlg._set_moment_from_x(dlg._ns_to_x(series[i][0], DAY))
        for i0, i1 in ((300, 700), (1500, 1900), (3000, 3400)):
            dlg._on_span(dlg._ns_to_x(series[i0][0], DAY),
                         dlg._ns_to_x(series[i1][0], DAY))
        # Open the day, which is what the list is for.
        dlg._day_list.expandAll()
        app.processEvents()
        pt = dlg._pick_table
        print(f"picks table: {pt.rowCount()} rows x {pt.columnCount()} cols")
        for r in range(pt.rowCount()):
            print("   ", " | ".join(
                (pt.item(r, c).text() if pt.item(r, c) is not None else "")
                for c in range(pt.columnCount())))
        print("picks label:", dlg._lbl_picks.text())
        print("search button:", dlg._btn_search.text())
        # NO ORPHAN CHILD ON THE WINDOW. A widget nobody put in a layout sits at
        # (0, 0) of the window at its own size, in the window's own ground, and
        # never scrolls — that was the invisible rectangle in the top-left corner
        # of this panel, left behind by the graph toolbar's light-palette host.
        from PySide6.QtWidgets import QWidget
        lay = dlg.layout()
        orphans = [w for w in dlg.findChildren(QWidget)
                   if w.parent() is dlg and w.isVisible()
                   and (lay is None or lay.indexOf(w) < 0)]
        print("orphan children at the window's top-left:",
              [(type(w).__name__, w.x(), w.y(), w.width(), w.height())
               for w in orphans] or "none")
        out = Path(__file__).with_name("pv_search.png")
        dlg.grab().save(str(out))
        out_r = Path(__file__).with_name("pv_search_picks.png")
        dlg._pick_table.grab().save(str(out_r))
        print("written:", out_r)
        # A second shot with the sidebar run down to its foot, so the PV list and
        # the condition rows below the fold are judged too.
        sb = dlg._side_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())
        app.processEvents()
        out2 = Path(__file__).with_name("pv_search_regions.png")
        dlg.grab().save(str(out2))
        print("written:", out2)
        sb.setValue(0)
        app.processEvents()
        for i in range(dlg._day_list.topLevelItemCount()):
            top = dlg._day_list.topLevelItem(i)
            print("day:", top.text(0))
            for j in range(top.childCount()):
                print("    ", top.child(j).text(0))
        print("status:", dlg._status.text())
        print("written:", out)

        # DELETING LEAVES THE NUMBERS ALONE. Take moment 2 and region 2 off and
        # the rest must still read 1, 3 — on the graph as well as in the tables —
        # until Renumber is pressed.
        dlg._delete_moment(dlg._moments[1])
        dlg._delete_region(dlg._regions[1]["id"])
        app.processEvents()
        def numbers():
            return [(pt.item(r, 0).text(), pt.item(r, 2).text())
                    for r in range(pt.rowCount())
                    if pt.item(r, 0) is not None]

        print("after delete:", numbers())
        print("after delete, button:", dlg._btn_search.text())
        out3 = Path(__file__).with_name("pv_search_deleted.png")
        dlg.grab().save(str(out3))
        print("written:", out3)
        dlg._renumber_picks()
        app.processEvents()
        print("after renumber:", numbers())
        app.quit()

    QTimer.singleShot(2500, shoot)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
