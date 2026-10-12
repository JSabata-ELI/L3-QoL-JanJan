"""Render the parts touched by the October wish list, for a look by eye.

Nothing appears on screen (real fonts via the minimal platform). Writes into
testing/_out/:

    wl_window.png          whole window: INFO pinned at the bottom of the
                           sidebar, the dead-shot tick box, the statistics
                           strip with its Start / End boxes
    wl_window_small.png    the same in a short window (INFO still visible)
    wl_time_window.png     the Start/End dialog in its default relative mode
    wl_saved_graph.png     a saved graph: heading, PV names, statistics

    python testing/probe_wishlist_look.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox   # noqa: E402

import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402
import test_selection_stats_rules as data        # noqa: E402

OUT = os.path.join(HERE, "_out")
os.makedirs(OUT, exist_ok=True)
_app = QApplication.instance() or QApplication([])


def _pump(seconds=0.0, cond=None):
    deadline = time.time() + seconds
    while True:
        _app.processEvents()
        if cond is not None and cond():
            return True
        if time.time() >= deadline:
            return cond() if cond is not None else True
        time.sleep(0.01)


def main():
    for mod in (cpva_core, css):
        for fn in ("cpva_fetch_samples", "cpva_fetch_samples_chunked"):
            if hasattr(mod, fn):
                setattr(mod, fn, data._fake)
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        setattr(css, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

    w = css.CSSLoggerWidget()
    w.resize(1500, 950)
    w._pv_list.clear()
    for pv in data.PVS:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    w._master_pv_edit.setText(data.WP_PV)
    w._master_multiple_edit.setText("")
    w._set_conditions([])
    w._custom_pvs = []
    w._dt_from, w._dt_to = data.T_FROM, data.T_TO
    w.show()
    w._on_load_clicked()
    _pump(20.0, lambda: (not w._load_in_flight) and w._graph_axes)
    _pump(0.5)
    a = data._num(data.T0_NS + 300 * data.NS)
    b = data._num(data.T0_NS + 600 * data.NS)
    w._on_span_select(a, b)
    _pump(0.5)
    w.grab().save(os.path.join(OUT, "wl_window.png"))
    w._write_graph_image(os.path.join(OUT, "wl_saved_graph.png"))
    w.resize(1500, 620)
    _pump(0.8)
    w.grab().save(os.path.join(OUT, "wl_window_small.png"))

    dlg = css.TimeWindowDialog(w, data.T_FROM, data.T_TO)
    dlg.show()
    _pump(0.4)
    dlg.grab().save(os.path.join(OUT, "wl_time_window.png"))
    print("time window size:", dlg.width(), "x", dlg.height())
    dlg.hide()
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    w.hide()
    print("written to", OUT)


if __name__ == "__main__":
    main()
    os._exit(0)
