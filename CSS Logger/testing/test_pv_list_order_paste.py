"""PV list: moving a PV moves its band even after rows were once dragged in
the PV table; Ctrl+V pastes PV names; INFO sits outside the scrolling sidebar.

Headless, network mocked:

    python testing/test_pv_list_order_paste.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import (QApplication, QDialog, QMessageBox,  # noqa: E402
                               QScrollArea)

import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []
PVS = ["L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-C:Energy"]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def _pump(seconds=0.0, cond=None):
    deadline = time.time() + seconds
    while True:
        _app.processEvents()
        if cond is not None and cond():
            return True
        if time.time() >= deadline:
            return cond() if cond is not None else True
        time.sleep(0.01)


def _fake(channel, start_ns, end_ns, *a, **k):
    span = max(int(end_ns) - int(start_ns), 1)
    return [{"time": int(start_ns) + span * i // 20, "value": 10.0 + i,
             "metaData": {"units": "J"}} for i in range(20)]


def main() -> int:
    print("test_pv_list_order_paste")
    for mod in (cpva_core, css):
        for fn in ("cpva_fetch_samples", "cpva_fetch_samples_chunked"):
            if hasattr(mod, fn):
                setattr(mod, fn, _fake)
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        setattr(css, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected

    w = css.CSSLoggerWidget()
    w.resize(1400, 900)
    w._pv_list.clear()
    for pv in PVS:
        w._pv_list.addItem(w._make_pv_item(pv))
    w._update_pv_count()
    w._master_multiple_edit.setText("")
    w._set_conditions([])
    w._custom_pvs = []
    w._on_load_clicked()
    ok = _pump(15.0, lambda: (not w._load_in_flight) and w._graph_axes)
    check("a graph is drawn", ok)
    if not ok:
        return 1

    # INFO is not inside the scrolling sidebar.
    p, inside = w._lbl_status.parentWidget(), False
    while p is not None:
        if isinstance(p, QScrollArea):
            inside = True
        p = p.parentWidget()
    check("INFO sits outside the sidebar's scroll area", not inside)

    # Rows once dragged in the PV table: an order of its own.
    w._plot_order = [PVS[2], PVS[1], PVS[0]]
    w._plot_graph()
    check("the dragged order is used", w._graph_pvs == [PVS[2], PVS[1], PVS[0]],
          str(w._graph_pvs))
    # Now move A down in the PV list: A, B, C -> B, A, C.
    w._pv_list.clearSelection()
    w._pv_list.item(0).setSelected(True)
    w._move_selected_pvs(1)
    _pump(0.3)
    check("Move down in the PV list moves the band",
          w._graph_pvs == [PVS[1], PVS[0], PVS[2]], str(w._graph_pvs))

    # Paste: several names, one already there.
    QApplication.clipboard().setText(f"L3-TEST-D:Energy\nL3-TEST-E:Energy, {PVS[0]}")
    reloads = []
    w._request_reload = lambda *a, **k: reloads.append(k)
    w._paste_pv_names()
    names = w._real_pv_names()
    check("pasted names are added, a duplicate is skipped",
          names[-2:] == ["L3-TEST-D:Energy", "L3-TEST-E:Energy"]
          and names.count(PVS[0]) == 1, str(names))
    check("and the new channels are loaded", len(reloads) == 1)

    w.close()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
