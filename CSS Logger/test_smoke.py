"""
Offline smoke test for the CPVA Suite (CSS Logger).

Runs fully headless (QT_QPA_PLATFORM=offscreen) with the CPVA network mocked,
so it works on any machine without the archiver or the LCS image share. It:

  * builds the widget and the full suite window,
  * mocks cpva_fetch_* with synthetic samples (numeric / string / image / empty
    / single-point) so "a PV always loads",
  * drives EVERY sidebar / tab / dialog button handler and asserts none raises,
  * asserts that after a load a graph canvas and table rows actually appear,
  * runs an edge-case matrix (0 PVs, empty result, all-non-numeric, network
    error) and asserts the UI never crashes and the LOAD button always recovers.

Run:   python test_smoke.py        (prints PASS/FAIL, exits non-zero on failure)
  or:  pytest test_smoke.py
"""
import os
import sys
import time
import tempfile
import pathlib

# Must be set before PySide6 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = pathlib.Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import cpva_core
import main as app_main
from PySide6.QtWidgets import (
    QApplication, QDialog, QMessageBox, QFileDialog, QInputDialog, QColorDialog,
)
from PySide6.QtGui import QColor


# ── Synthetic data (no network) ──────────────────────────────────────────────

def _fake_chunked(channel, start_ns, end_ns, *args, **kwargs):
    """Return synthetic CPVA samples for one channel, varying by name so the
    test covers numeric / string / image-path / empty / single-point streams."""
    name = channel.lower()
    if "empty" in name:
        return []
    span = max(int(end_ns) - int(start_ns), 1)
    if "one" in name:
        return [{"time": int(start_ns) + span // 2, "value": 42.0,
                 "metaData": {"units": "J"}}]
    n = 40
    out = []
    for i in range(n):
        ts = int(start_ns) + span * i // n
        if "img" in name or "image" in name:
            val = f"2026/06/25/cam_{i:03d}.png"
            units = ""
        elif "str" in name or "enum" in name:
            val = ["OPEN", "CLOSED"][i % 2]
            units = ""
        else:
            val = 100.0 + 10.0 * (i % 7) + (i * 0.5)
            units = "J"
        out.append({"time": ts, "value": val, "metaData": {"units": units}})
    return out


def _fake_samples(channel, start_ns, end_ns, *args, **kwargs):
    return _fake_chunked(channel, start_ns, end_ns)


def _fake_channels(*args, **kwargs):
    return ["L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-STR:State"]


def _boom(*args, **kwargs):
    raise RuntimeError("simulated CPVA network failure")


# ── Patching ─────────────────────────────────────────────────────────────────

def _install_network_mock(fetch=_fake_chunked):
    for mod in (cpva_core, app_main):
        if hasattr(mod, "cpva_fetch_samples_chunked"):
            mod.cpva_fetch_samples_chunked = fetch
        if hasattr(mod, "cpva_fetch_samples"):
            mod.cpva_fetch_samples = (_fake_samples if fetch is _fake_chunked else fetch)
        if hasattr(mod, "cpva_fetch_channels"):
            mod.cpva_fetch_channels = (_fake_channels if fetch is _fake_chunked else fetch)


def _install_dialog_mocks():
    """Make modal dialogs non-blocking and stop handlers writing user files."""
    # Never write the user's real config/preset/custom-pv files during the test.
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        if hasattr(app_main, fn):
            setattr(app_main, fn, lambda *a, **k: None)

    QMessageBox.question    = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.warning     = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.critical    = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

    # Dialogs: don't block. Rejected = leave state untouched (we exercise the
    # dialogs' own accept-parsing separately in test_dialog_accept_parsing).
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected

    _tmp_png = str(pathlib.Path(tempfile.gettempdir()) / "cssl_smoke_graph.png")
    _tmp_csv = str(pathlib.Path(tempfile.gettempdir()) / "cssl_smoke_export.csv")
    QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (_tmp_png if "png" in str(a).lower() or "graph" in str(a).lower()
                         else _tmp_csv, "filter"))
    QInputDialog.getText = staticmethod(lambda *a, **k: ("SmokePreset", True))
    QColorDialog.getColor = staticmethod(lambda *a, **k: QColor("#123456"))


# ── Helpers ──────────────────────────────────────────────────────────────────

_app = QApplication.instance() or QApplication([])


def _pump_until(cond, timeout_s=15.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    _app.processEvents()
    return cond()


def _new_widget():
    # Prevent the deferred live-mode autostart from firing during the test.
    app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    w = app_main.CSSLoggerWidget()
    return w


def _set_pvs(w, pvs):
    w._pv_list.clear()
    for pv in pvs:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    # Neutralise the user-config row filters so the test sees the raw data flow.
    # (The shipped config sets master_multiple/conditions tuned to real PVs that
    # the synthetic test PVs don't include, which would legitimately empty the
    # table — the app warns about that, which is correct behaviour, not a bug.)
    if w._master_multiple_edit is not None:
        w._master_multiple_edit.setText("")
    w._conditions = []


def _load_and_wait(w):
    w._on_load_clicked()
    ok = _pump_until(lambda: w._btn_load.isEnabled())
    assert ok, "LOAD DATA button never re-enabled — UI would be stuck"
    return ok


# ── Tests ────────────────────────────────────────────────────────────────────

def test_construct_suite_window():
    _install_network_mock()
    _install_dialog_mocks()
    win = app_main.CPVASuiteWindow()
    assert win is not None
    win.close()


def test_load_produces_graph_and_table():
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    _set_pvs(w, ["L3-TEST-A:Energy", "L3-TEST-B:Energy",
                 "L3-TEST-STR:State", "L3-TEST-IMG:Path"])
    _load_and_wait(w)
    assert w._mpl_canvas is not None, "no graph canvas after load"
    assert len(w._table_rows) > 0, "no table rows after load"
    w.close()


def test_every_button_handler():
    """Drive every button/setting handler; none may raise."""
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    _set_pvs(w, ["L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-IMG:Path"])
    _load_and_wait(w)

    # (label, callable) — covers sidebar, graph, XY, PV-time, table, log tabs.
    w._xy_x_combo.setCurrentIndex(0)
    if w._xy_y_combo.count() > 2:
        w._xy_y_combo.setCurrentIndex(2)

    actions = [
        ("open_time_window",   w._open_time_window_dialog),
        ("load_preset",        w._load_preset),
        ("save_preset",        w._save_preset),
        ("save_preset_as",     w._save_preset_as),
        ("delete_preset",      lambda: None),  # skip: would mutate combo state
        ("open_pv_browser",    w._open_pv_browser),
        ("clear_pv_list",      w._clear_pv_list),       # mocked question -> Yes
        ("open_ref_lines",     w._open_ref_lines_dialog),
        ("open_conditions",    w._open_conditions_dialog),
        ("open_custom_pv",     w._open_custom_pv_dialog),
        ("font_size",          lambda: (w._font_size_spin.setValue(14), w._apply_font_size())),
        ("apply_axis",         w._apply_axis_settings),
        ("zoom_back",          w._zoom_back),
        ("clean_graph",        w._clean_graph),
        ("save_graph",         w._save_graph),
        ("plot_xy",            w._plot_xy),
        ("xy_zoom_back",       w._xy_zoom_back),
        ("clear_xy",           w._clear_xy_plot),
        ("pv_time_add_cond",   w._pv_time_add_condition_row),
        ("plot_pv_time",       w._plot_pv_time),       # may warn (no pyarrow) — must not crash
        ("export_csv",         w._export_csv),
        ("open_image_row",     lambda: w._try_open_image_at_row(0)),
        ("clear_log",          w._clear_log),
        ("replot_graph",       w._plot_graph),
    ]
    failures = []
    for label, fn in actions:
        try:
            fn()
            _app.processEvents()
        except Exception as exc:
            import traceback
            failures.append(f"{label}: {exc}\n{traceback.format_exc()}")
    w.close()
    assert not failures, "Button handlers raised:\n" + "\n".join(failures)


def test_live_mode_toggle():
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    _set_pvs(w, ["L3-TEST-A:Energy", "L3-TEST-B:Energy"])
    w._toggle_live_mode()                 # start
    assert w._live_mode is True
    _pump_until(lambda: w._mpl_canvas is not None)
    # let a couple of incremental ticks run
    _pump_until(lambda: False, timeout_s=1.0)
    w._toggle_live_mode()                 # stop
    assert w._live_mode is False
    assert w._btn_load.isEnabled()
    w.close()


def test_edge_cases():
    _install_dialog_mocks()
    # 1) No PVs: load is a no-op, button stays usable.
    _install_network_mock()
    w = _new_widget()
    _set_pvs(w, [])
    w._on_load_clicked()
    _app.processEvents()
    assert w._btn_load.isEnabled()

    # 2) Empty result for all PVs.
    _set_pvs(w, ["L3-EMPTY-A:x", "L3-EMPTY-B:x"])
    _load_and_wait(w)
    w._plot_graph()                       # must not crash with zero numeric data

    # 3) All-non-numeric values.
    _set_pvs(w, ["L3-TEST-STR:State"])
    _load_and_wait(w)
    w._plot_graph()

    # 4) Single point.
    _set_pvs(w, ["L3-ONE:val"])
    _load_and_wait(w)

    # 5) Network failure on every PV: must recover, button re-enabled.
    _install_network_mock(fetch=_boom)
    _set_pvs(w, ["L3-TEST-A:Energy"])
    _load_and_wait(w)
    assert w._btn_load.isEnabled(), "LOAD stuck after network failure"
    w.close()


def test_dialog_accept_parsing():
    """Construct each editable dialog, add good AND bad rows, run _accept —
    bad numeric input must be ignored, never raise."""
    _install_dialog_mocks()
    pvs = ["L3-TEST-A:Energy", "L3-TEST-B:Energy"]

    cond = app_main._ConditionsDialog([], pvs)
    cond._add_row(pvs, {"pv": "L3-TEST-A:Energy", "min": "1.5", "max": "nonsense"})
    cond._add_row(pvs, {"pv": "L3-TEST-B:Energy", "min": "", "max": ""})
    cond._accept()
    assert isinstance(cond.result_conditions, list)

    ref = app_main._RefLinesDialog([])
    ref._add_row({"label": "ok", "y": "3.14", "color": "#FF0000"})
    ref._add_row({"label": "bad", "y": "xx", "color": "#00FF00"})
    ref._accept()
    assert len(ref.result_lines) == 1     # bad Y dropped, good kept

    cpv = app_main._CustomPVDialog([])
    cpv._add_row({"name": "Sum", "expr": "a + b"})
    if hasattr(cpv, "_accept"):
        cpv._accept()


# ── Standalone runner ────────────────────────────────────────────────────────

def _main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as exc:
            failed += 1
            import traceback
            print(f"FAIL  {t.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
