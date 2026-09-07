"""Probe: is the Spectra tab's "remember the user's zoom" wiring still alive?

It listens on the axes' own notification list. ax.clear() replaces that list, and
the graph is cleared on every redraw -- so the listener may be talking to a dead
list from the first redraw on.

Run:  python testing/probe_zoom_memory.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PySide6.QtWidgets import QApplication

from sp_t import SpectraWidget
from testing.probe_ylim_range import _region, X  # noqa: E402


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    _ = app
    w = SpectraWidget()
    w._x_data = X
    w._chk_autofit.setChecked(False)
    w._sb_x_min.setValue(1330)
    w._sb_x_max.setValue(2810)
    ax = w._ax_bot
    reg_at_start = id(ax.callbacks)

    w._regions = [_region(1, 40, 2050, 1, "#455A64", 0)]
    w._region_seq = 1
    w._rebuild_regions_ui()
    w._cmb_method.setCurrentIndex(w._cmb_method.findText("Every spectrum"))
    QApplication.processEvents()
    w._redraw_spectra()
    QApplication.processEvents()

    print("notification list at start :", reg_at_start)
    print("notification list now      :", id(ax.callbacks),
          "(same)" if id(ax.callbacks) == reg_at_start else
          "(REPLACED by ax.clear -> the listeners must be re-attached)")

    # A limit change with nobody behind it: matplotlib does this itself while an
    # axes is drawn, and it must NOT be taken for the user's zoom.
    w._bot_user_ylim = None
    ax.set_ylim(0.2, 0.4)
    QApplication.processEvents()
    print("no gesture, remembered     :", w._bot_user_ylim, "(want None)")

    # What the toolbar really does: Zoom is the active tool while the limits move.
    w._tb_bot.mode = "zoom rect"
    ax.set_ylim(0.2, 0.4)
    QApplication.processEvents()
    w._tb_bot.mode = ""
    print("after a real zoom          :", w._bot_user_ylim)
    w._redraw_spectra()
    QApplication.processEvents()
    print("after the next redraw, axis:", ax.get_ylim(), "(want the zoom back)")


if __name__ == "__main__":
    main()
