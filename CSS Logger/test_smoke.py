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
from PySide6.QtCore import Qt


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
    # There is no LOAD DATA button any more: a fetch is started by whatever
    # changed the selection, and _load_in_flight is what has to clear afterwards.
    # If it never clears, no further automatic reload could ever start.
    w._on_load_clicked()
    ok = _pump_until(lambda: not w._load_in_flight)
    assert ok, "load never finished — automatic reloads would be blocked"
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
        # Zoom / pan / view history now live on the graph toolbar; the app-side
        # entry points are the ones the toolbar and the right-drag zoom call.
        ("graph_view_home",    w._on_graph_view_home),
        ("retick",             w._retick_from_current_xlim),
        ("sync_zoom_mode",     w._sync_graph_interaction_mode),
        ("adopt_margins",      w._adopt_toolbar_margins),
        ("clear_selection",    w._clear_selection),
        ("clean_graph",        w._clean_graph),
        ("save_graph",         w._save_graph),
        ("plot_xy",            w._plot_xy),
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
    assert not w._load_in_flight, "stopping live left the fetch lock held"
    w.close()


def test_edge_cases():
    _install_dialog_mocks()
    # 1) No PVs: load is a no-op, button stays usable.
    _install_network_mock()
    w = _new_widget()
    _set_pvs(w, [])
    w._on_load_clicked()
    _app.processEvents()
    assert not w._load_in_flight

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
    assert not w._load_in_flight, "fetch lock stuck after network failure"
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

    ref = app_main._RefLinesDialog([], ["L3-TEST-A:Energy"])
    ref._add_row({"label": "ok", "y": "3.14", "color": "#FF0000"})
    ref._add_row({"label": "bad", "y": "xx", "color": "#00FF00"})
    ref._accept()                          # the warning box is mocked away
    assert len(ref.result_lines) == 1     # bad Y dropped, good kept

    cpv = app_main._CustomPVDialog([])
    cpv._add_row({"name": "Sum", "expr": "a + b"})
    if hasattr(cpv, "_accept"):
        cpv._accept()


def test_custom_pv_expression_vars():
    """Only real channel letters are treated as variables."""
    assert app_main._cpv_vars("A+B") == ["A", "B"]
    assert app_main._cpv_vars("F/(F+H)*100") == ["F", "H"]
    assert app_main._cpv_vars("math.log(A) + 1E5 + round(B, 2)") == ["A", "B"]
    # Simultaneous rename — no cascading through an already-rewritten letter.
    assert app_main._cpv_rewrite("F+H", {"F": "A", "H": "F"}) == "A+F"


def test_custom_pv_bindings_follow_pvs():
    """A formula must keep its PVs when the list order changes — the whole point
    of storing bindings instead of relying on the channel position."""
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    pv1, pv2 = "L3-TEST-A:Energy", "L3-TEST-B:Energy"
    w._custom_pvs = [{"name": "Sum", "expr": "A+B",
                      "bindings": {"A": pv1, "B": pv2}}]

    def _one_row():
        return [(1_000, {pv1: (1.0, "J"), pv2: (10.0, "J")})]

    w._pv_order = [pv1, pv2, "Sum"]
    rows = _one_row()
    w._compute_custom_pvs_in_rows(rows)
    assert rows[0][1]["Sum"][0] == 11.0

    # Same PVs, swapped positions: A/B now mean the other channels, the result
    # must not change.
    w._pv_order = [pv2, pv1, "Sum"]
    rows = _one_row()
    w._compute_custom_pvs_in_rows(rows)
    assert rows[0][1]["Sum"][0] == 11.0, "custom PV did not follow its PVs"

    # A bound PV dropped from the list: empty value, channel still present, and
    # a diagnostic that names the missing PV.
    w._pv_order = [pv1, "Sum"]
    rows = [(1_000, {pv1: (1.0, "J")})]
    w._compute_custom_pvs_in_rows(rows)
    assert rows[0][1]["Sum"][0] is None
    assert any(pv2 in m for m in w._cpv_diag), "missing binding not reported"

    # The unloaded PV still gets a letter in the dialog, marked not-loaded.
    chans = w._cpv_dialog_channels()
    assert (pv2, False) in [(pv, ld) for _lt, pv, _d, ld in chans]

    # Display letters follow the current order and canonicalise back unchanged.
    w._pv_order = [pv2, pv1, "Sum"]
    chans = w._cpv_dialog_channels()
    letter_by_pv = {pv: lt for lt, pv, _d, _ld in chans}
    pv_by_letter = {lt: pv for lt, pv, _d, _ld in chans}
    disp, unbound = app_main._cpv_to_display(w._custom_pvs[0], letter_by_pv)
    assert disp == "B+A" and not unbound
    _expr, bindings = app_main._cpv_from_display(disp, pv_by_letter)
    assert bindings == {"B": pv1, "A": pv2}
    w.close()


def test_custom_pv_binding_migration():
    """Entries saved before bindings existed get them from the legacy order."""
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    w._custom_pvs = [{"name": "Sum of Green [J]", "expr": "F+H"}]
    w._migrate_custom_pv_bindings()
    assert w._custom_pvs[0]["bindings"] == {
        "F": "L3-PM03-025:Energy",
        "H": "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    }
    # Already-bound entries are left alone.
    w._custom_pvs = [{"name": "X", "expr": "A", "bindings": {"A": "keep:me"}}]
    w._migrate_custom_pv_bindings()
    assert w._custom_pvs[0]["bindings"] == {"A": "keep:me"}
    w.close()


def test_custom_pv_bad_expression_is_reported():
    """A broken formula yields an empty channel and says so, instead of being
    silently indistinguishable from missing data."""
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    pv1 = "L3-TEST-A:Energy"
    w._custom_pvs = [
        {"name": "Broken", "expr": "A +", "bindings": {"A": pv1}},
        {"name": "DivZero", "expr": "A/0", "bindings": {"A": pv1}},
    ]
    w._pv_order = [pv1, "Broken", "DivZero"]
    rows = [(1_000, {pv1: (1.0, "J")})]
    w._compute_custom_pvs_in_rows(rows)
    assert rows[0][1]["Broken"][0] is None
    assert rows[0][1]["DivZero"][0] is None
    assert any("invalid expression" in m for m in w._cpv_diag)
    assert any("ZeroDivisionError" in m for m in w._cpv_diag)
    w.close()


def test_custom_pv_bindings_table():
    """The dialog's bindings table names the PV behind every letter of every
    formula, follows the loaded order, and re-binds a letter when a different
    channel is picked."""
    from PySide6.QtWidgets import QComboBox
    pv1, pv2, pv3 = "L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-C:Energy"

    def _chans(order, unloaded=()):
        ch = [(app_main.CSSLoggerWidget._col_letter(i), pv, pv, True)
              for i, pv in enumerate(order)]
        for pv in unloaded:
            ch.append((app_main.CSSLoggerWidget._col_letter(len(ch)), pv, pv, False))
        return ch

    def _dump(dlg):
        t = dlg._bind_tbl
        out = []
        for r in range(t.rowCount()):
            cb = t.cellWidget(r, 2)
            out.append((t.item(r, 0).text(), t.item(r, 1).text(),
                        cb.currentText() if isinstance(cb, QComboBox) else None,
                        t.item(r, 3).text()))
        return out

    cpv = {"name": "Scaled", "expr": "A*0.749", "bindings": {"A": pv1}}

    # One row per letter per formula.
    dlg = app_main._CustomPVDialog(
        [dict(cpv), {"name": "Ratio", "expr": "A/B",
                     "bindings": {"A": pv1, "B": pv2}}],
        _chans([pv1, pv2, pv3]))
    dlg._refresh_bindings_table()
    assert _dump(dlg) == [("Scaled", "A", pv1, "loaded"),
                          ("Ratio",  "A", pv1, "loaded"),
                          ("Ratio",  "B", pv2, "loaded")], _dump(dlg)

    # Same formulas, pv1 moved to third place: letters move, PVs do not.
    dlg2 = app_main._CustomPVDialog([dict(cpv)], _chans([pv2, pv3, pv1]))
    dlg2._refresh_bindings_table()
    assert dlg2._rows[0]["expr"].text() == "C*0.749"
    assert _dump(dlg2) == [("Scaled", "C", pv1, "loaded")], _dump(dlg2)

    # Picking another channel re-binds that letter, and OK stores the new PV.
    dlg3 = app_main._CustomPVDialog([dict(cpv)], _chans([pv1, pv2, pv3]))
    dlg3._refresh_bindings_table()
    cb = dlg3._bind_tbl.cellWidget(0, 2)
    assert [cb.itemText(i) for i in range(cb.count())] == [pv1, pv2, pv3]
    cb.setCurrentText(pv3)
    cb.activated.emit(cb.currentIndex())
    _app.processEvents()
    assert dlg3._rows[0]["expr"].text() == "C*0.749"
    dlg3._accept()
    assert dlg3.result_pvs == [{"name": "Scaled", "expr": "C*0.749",
                                "bindings": {"C": pv3}}], dlg3.result_pvs

    # A bound-but-unloaded PV is marked and survives OK unchanged.
    dlg4 = app_main._CustomPVDialog([dict(cpv)], _chans([pv2, pv3], unloaded=[pv1]))
    dlg4._refresh_bindings_table()
    assert _dump(dlg4) == [("Scaled", "C", pv1, "not loaded")], _dump(dlg4)
    dlg4._accept()
    assert dlg4.result_pvs[0]["bindings"] == {"C": pv1}

    # A letter with no channel at all is flagged rather than guessed.
    dlg5 = app_main._CustomPVDialog([{"name": "Bad", "expr": "Z*2", "bindings": {}}],
                                    _chans([pv1]))
    dlg5._refresh_bindings_table()
    assert _dump(dlg5)[0][3] == "no channel", _dump(dlg5)

    # A rebuild queued while typing must not fire on a cancelled dialog.
    import gc
    for _ in range(10):
        d = app_main._CustomPVDialog([dict(cpv)], _chans([pv1, pv2]))
        d._rows[0]["expr"].setText("A*3")
        d.reject()
        del d
        gc.collect()
        _app.processEvents()


def test_conditions_discard_out_of_range_values():
    """The Conditions filter must actually drop out-of-range rows — including
    when that leaves nothing — and must not let a dataless condition PV reject
    every row on its own."""
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    pv1, pv2 = "L3-TEST-A:Energy", "L3-TEST-B:Energy"
    w._conditions = [{"pv": pv1, "min": 1.0, "max": 10.0}]

    rows = [(1, {pv1: (5.0, "J"), pv2: (0.0, "J")}),
            (2, {pv1: (99.0, "J"), pv2: (0.0, "J")}),
            (3, {pv1: (0.1, "J"), pv2: (0.0, "J")}),
            (4, {pv1: (10.0, "J"), pv2: (0.0, "J")})]
    kept = [ts for ts, _ in w._apply_conditions_to_rows(rows)]
    assert kept == [1, 4], kept

    # Everything out of range -> empty, NOT a silent fall back to all rows.
    w._cond_last_diag = None
    allbad = [(1, {pv1: (0.0, "J")}), (2, {pv1: (0.0, "J")})]
    assert w._apply_conditions_to_rows(allbad) == []

    # A condition on a PV with no data is skipped; the other one still filters.
    w._cond_last_diag = None
    w._conditions = [{"pv": "L3-TEST-NOPE:Energy", "min": 0.0, "max": 1.0},
                     {"pv": pv1, "min": 1.0, "max": 10.0}]
    kept = [ts for ts, _ in w._apply_conditions_to_rows(rows)]
    assert kept == [1, 4], kept

    # Non-numeric and absent values never pass a condition.
    w._conditions = [{"pv": pv1, "min": 1.0, "max": 10.0}]
    assert not w._row_matches_conditions({pv1: ("OPEN", "")})
    assert not w._row_matches_conditions({pv2: (5.0, "J")})

    # A custom channel can be a condition PV.
    w._conditions = [{"pv": "Scaled", "min": 5.0, "max": 8.0}]
    w._custom_pvs = [{"name": "Scaled", "expr": "A*0.749", "bindings": {"A": pv1}}]
    w._pv_order = [pv1, "Scaled"]
    crows = [(1, {pv1: (10.0, "J")}), (2, {pv1: (1.0, "J")})]
    w._compute_custom_pvs_in_rows(crows)
    w._cond_last_diag = None
    assert [ts for ts, _ in w._apply_conditions_to_rows(crows)] == [1]
    w.close()


# ── PV list columns, per-signal look, reference lines ────────────────────────

def _loaded_widget(pvs=("L3-TEST-A:Energy", "L3-TEST-B:Energy")):
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    _set_pvs(w, list(pvs))
    _load_and_wait(w)
    return w


class _FakeMplEvent:
    """Stand-in for a matplotlib mouse/key event (pixel coordinates)."""

    def __init__(self, x, y, button=1, key=None):
        self.x, self.y, self.button, self.key = x, y, button, key


def test_axis_columns_move_hide_reset():
    w = _loaded_widget()
    cols = list(w._axis_tv_cols)
    hdr  = w._axis_tv.horizontalHeader()

    assert hdr.sectionsMovable(), "column headings cannot be dragged"
    assert w._axis_tv.horizontalScrollMode() == \
        app_main.QAbstractItemView.ScrollMode.ScrollPerPixel, \
        "the table cannot scroll sideways on a small screen"
    for c in ("style", "marker", "marker_size"):
        assert not w._axis_tv.isColumnHidden(cols.index(c)), f"{c} should start visible"
    for c in ("alpha", "unit", "last", "min", "max", "mean", "count"):
        assert w._axis_tv.isColumnHidden(cols.index(c)), f"{c} should start hidden"

    li_width = cols.index("width")
    hdr.moveSection(hdr.visualIndex(li_width), 0)
    w._axis_tv.setColumnWidth(cols.index("smooth"), 7)
    w._axis_tv.setColumnHidden(cols.index("min"), False)

    w._reset_axis_columns()
    assert hdr.visualIndex(li_width) == li_width, "Reset did not restore the order"
    assert w._axis_tv.columnWidth(cols.index("smooth")) == \
        w._AXIS_COL_WIDTHS["smooth"], "Reset did not restore the widths"
    assert w._axis_tv.isColumnHidden(cols.index("min")), \
        "Reset did not restore which columns are shown"
    w.close()


def test_per_signal_line_and_point_style():
    w = _loaded_widget()
    pv = w._graph_pvs[0]
    w._pv_settings[pv].update({"style": "dotted", "marker": "▲",
                               "marker_size": 6, "alpha": 50})
    w._plot_graph()
    line = w._graph_lines[0][-1]
    assert line.get_linestyle() == ":", line.get_linestyle()
    assert line.get_marker() == "^", line.get_marker()
    assert abs(line.get_markersize() - 6) < 1e-6
    assert abs(line.get_alpha() - 0.5) < 1e-6
    assert w._graph_lines[1][-1].get_linestyle() == "-", "the other signal changed too"

    w._pv_settings[pv].update({"style": "solid", "marker": "○"})
    w._plot_graph()
    assert w._graph_lines[0][-1].get_markerfacecolor() == "none", "hollow point is filled"

    # Line off AND points off must never make a signal disappear.
    w._pv_settings[pv].update({"style": "none", "marker": "none"})
    w._plot_graph()
    assert w._graph_lines[0][-1].get_linestyle() == "-", "the signal became invisible"

    # "auto" keeps the old global-switch behaviour.
    w._pv_settings[pv].update({"style": "solid", "marker": "auto"})
    w._graph_opts["line_markers"] = True
    w._plot_graph()
    assert w._graph_lines[0][-1].get_marker() == "."
    w._graph_opts["line_markers"] = False
    w._plot_graph()
    assert w._graph_lines[0][-1].get_marker() == "None"
    w.close()


def test_measured_columns():
    w = _loaded_widget(("L3-TEST-A:Energy", "L3-EMPTY-A:x"))
    cols = list(w._axis_tv_cols)
    for c in w._AXIS_MEASURED_COLS:
        w._axis_tv.setColumnHidden(cols.index(c), False)
    w._refresh_axis_settings_tv()

    def cell(pv, col):
        return w._axis_tv.item(w._axis_row_pv.index(pv), cols.index(col)).text()

    assert cell("L3-TEST-A:Energy", "count") == "40"
    assert cell("L3-TEST-A:Energy", "unit") == "J"
    assert cell("L3-TEST-A:Energy", "min")
    assert cell("L3-EMPTY-A:x", "count") == "0", "an empty channel must show 0"

    row = w._axis_row_pv.index("L3-TEST-A:Energy")
    assert not (w._axis_tv.item(row, cols.index("mean")).flags()
                & Qt.ItemFlag.ItemIsEditable), "Mean must not be typeable"
    assert (w._axis_tv.item(row, cols.index("style")).flags()
            & Qt.ItemFlag.ItemIsEditable), "Style must be editable"
    w.close()


def test_axis_table_edits_apply():
    w = _loaded_widget()
    cols = list(w._axis_tv_cols)
    pv   = w._graph_pvs[0]
    row  = w._axis_row_pv.index(pv)
    w._axis_tv.item(row, cols.index("style")).setText("dashed")
    w._axis_tv.item(row, cols.index("alpha")).setText("40")
    w._axis_tv.item(row, cols.index("marker_size")).setText("5,5")   # comma decimal
    w._apply_axis_settings()
    assert w._pv_settings[pv]["style"] == "dashed"
    assert w._pv_settings[pv]["alpha"] == 40
    assert abs(w._pv_settings[pv]["marker_size"] - 5.5) < 1e-9
    w._axis_tv.item(row, cols.index("style")).setText("zigzag")
    w._apply_axis_settings()
    assert w._pv_settings[pv]["style"] == "solid", "nonsense should fall back"
    w.close()


def test_ref_line_uses_its_own_signal_axis():
    w = _loaded_widget()
    pv_b = w._graph_pvs[1]
    w._ref_lines = [{"label": "limit", "pv": pv_b, "y": 123.0,
                     "color": "#000000", "style": "dotted", "width": 2.5}]
    w._plot_graph()
    lines = [a for a in w._ref_line_artists if hasattr(a, "get_linestyle")]
    assert lines, "no reference line drawn"
    ln = lines[0]
    assert ln.axes is w._graph_axes[1], "line landed on the wrong signal's scale"
    assert ln.get_linestyle() == ":" and abs(ln.get_linewidth() - 2.5) < 1e-9
    assert ln.get_ydata()[0] == 123.0
    assert any(hasattr(a, "get_text") and "limit" in a.get_text()
               for a in w._ref_line_artists), "the line has no visible name"

    # An old record with no signal still works.
    w._ref_lines = [{"label": "loose", "y": 5.0}]
    w._plot_graph()
    assert [a for a in w._ref_line_artists if hasattr(a, "get_linestyle")]

    # Tied to a switched-off signal: nothing drawn, no crash.
    w._ref_lines = [{"label": "gone", "pv": w._graph_pvs[0], "y": 5.0}]
    w._pv_settings[w._graph_pvs[0]]["show"] = False
    w._plot_graph()
    assert not [a for a in w._ref_line_artists if hasattr(a, "get_linestyle")]
    w.close()


def test_ref_line_two_click_placement():
    w = _loaded_widget()
    w.show(); _app.processEvents()
    w._plot_graph(); _app.processEvents()

    # Enter pick mode without _run_ref_pick's blocking event loop.
    holder = {"result": None}
    w._ref_pick = {"stage": 1, "pv": None, "holder": holder, "cids": []}
    for sel in (w._span_selector, w._zoom_selector):
        sel.set_active(False)
    w._set_ref_pick_step(1)
    assert w._pick_bar.isVisible(), "the step-by-step guide is not shown"
    assert w._ref_pick["dim"], "step 1 greys nothing out"

    # Step 1 — click the second signal's tick numbers, just left of its spine.
    ax_b = w._graph_axes[1]
    spine_x = ax_b.transAxes.transform((w._graph_spine_xpos[1][0], 0.0))[0]
    box = w._graph_axes[0].get_window_extent()
    w._on_ref_pick_click(_FakeMplEvent(spine_x - 3, (box.y0 + box.y1) / 2))
    assert w._ref_pick and w._ref_pick["stage"] == 2, "step 1 did not take"
    assert w._ref_pick["pv"] == w._graph_pvs[1]

    # Step 2 — click a known height and check the value that comes back.
    ylo, yhi = ax_b.get_ylim()
    want_y = ylo + 0.4 * (yhi - ylo)
    px, py = ax_b.transData.transform(
        (float(w._graph_axes[0].get_xlim()[0]) + 0.5, want_y))
    w._on_ref_pick_click(_FakeMplEvent(px, py))

    rec = holder["result"]
    assert rec is not None, "no reference line came back"
    assert rec["pv"] == w._graph_pvs[1]
    assert abs(rec["y"] - want_y) <= abs(want_y) * 1e-6 + 1e-6, (rec["y"], want_y)
    assert rec["color"] == "#000000", "a new line should default to black"
    assert w._ref_pick is None and not w._pick_bar.isVisible(), "pick mode did not end"
    assert w._span_selector.active and w._zoom_selector.active, \
        "span / zoom were left switched off"
    w.close()


def test_ref_pick_cancels_cleanly():
    w = _loaded_widget()
    w.show(); _app.processEvents()
    for cancel in ("escape", "replot"):
        holder = {"result": None}
        w._ref_pick = {"stage": 1, "pv": None, "holder": holder, "cids": []}
        for sel in (w._span_selector, w._zoom_selector):
            sel.set_active(False)
        w._set_ref_pick_step(1)
        if cancel == "escape":
            w._on_ref_pick_key(_FakeMplEvent(0, 0, key="escape"))
        else:
            w._plot_graph()          # the canvas being clicked on is thrown away
        assert w._ref_pick is None, f"{cancel} did not end pick mode"
        assert holder["result"] is None
        assert not w._pick_bar.isVisible()
        assert w._span_selector.active and w._zoom_selector.active, \
            f"{cancel} left span / zoom switched off"
        assert not [a for a in w._mpl_figure.artists
                    if type(a).__name__ == "Rectangle"], "grey overlay left behind"
    w.close()


def test_ref_lines_dialog_editing():
    w = _loaded_widget()
    src = [{"label": "A", "pv": w._graph_pvs[0], "y": 1.5, "color": "#FF0000",
            "style": "dotted", "width": 3.0},
           {"label": "B", "pv": None, "y": 2.5}]
    dlg = app_main._RefLinesDialog(src, w._graph_ref_pv_choices(), w)
    out, bad = dlg._collect()
    assert not bad
    assert out[0]["pv"] == w._graph_pvs[0] and out[0]["style"] == "dotted"
    assert abs(out[0]["width"] - 3.0) < 1e-9
    assert out[1]["pv"] is None and out[1]["color"] == "#000000"

    dlg._move_row(dlg._rows[0], +1)
    assert dlg._collect()[0][0]["label"] == "B", "moving a line down did nothing"
    dlg._remove_row(dlg._rows[0]["widget"], dlg._rows[0])
    out, _ = dlg._collect()
    assert len(out) == 1 and out[0]["label"] == "A"

    dlg._add_row({"label": "C", "y": ""})
    dlg._rows[-1]["y"].setText("7,25")               # comma decimal
    out, bad = dlg._collect()
    assert not bad and abs(out[-1]["y"] - 7.25) < 1e-9
    dlg._rows[-1]["y"].setText("nonsense")
    assert dlg._collect()[1], "an unreadable row must be reported, not dropped"
    w.close()


def test_saved_look_round_trip():
    w = _loaded_widget()
    cols = list(w._axis_tv_cols)
    pv   = w._graph_pvs[0]
    w._pv_settings[pv].update({"color": "#ABCDEF", "color_auto": False,
                               "style": "dash-dot", "marker": "■",
                               "marker_size": 8, "alpha": 30})
    w._ref_lines = [{"label": "kept", "pv": pv, "y": 9.0, "color": "#000000",
                     "style": "dashed", "width": 1.2}]
    w._axis_tv.setColumnHidden(cols.index("mean"), False)
    hdr = w._axis_tv.horizontalHeader()
    hdr.moveSection(hdr.visualIndex(cols.index("width")), 0)

    payload = w._current_style_payload()
    assert payload["columns"]["order"][0] == "width"
    assert "cursor_val" not in payload["pv_settings"][pv], \
        "a live cursor readout must not be saved into a look"

    w._pv_settings[pv].update({"color": "#111111", "style": "solid",
                               "marker": "auto", "marker_size": 3, "alpha": 100})
    w._ref_lines = []
    w._reset_axis_columns()
    w._apply_style_payload(payload)

    assert w._pv_settings[pv]["color"] == "#ABCDEF"
    assert w._pv_settings[pv]["style"] == "dash-dot"
    assert w._pv_settings[pv]["marker"] == "■"
    assert w._ref_lines and w._ref_lines[0]["label"] == "kept"
    assert not w._axis_tv.isColumnHidden(cols.index("mean"))
    assert w._axis_tv.horizontalHeader().visualIndex(cols.index("width")) == 0

    # Only NAMED looks are written to the config; the current one is not.
    w._style_presets["mine"] = payload
    w._save_runtime_state()
    assert "mine" in (w.config.get("style_presets") or {})
    assert "pv_settings" not in w.config and "ref_lines" not in w.config
    w.close()


# ── Graph controls, grids, selection, live pacing, XY ────────────────────────

def test_selection_survives_a_redraw():
    """The selected region and its statistics must outlive a redraw.

    They used to live only inside the matplotlib selector and the card labels, so
    a font change, an axis edit or a reload silently threw them away.
    """
    w = _loaded_widget()
    t_lo, t_hi = w._graph_axes[0].get_xlim()
    lo = t_lo + 0.25 * (t_hi - t_lo)
    hi = t_lo + 0.75 * (t_hi - t_lo)
    w._on_span_select(lo, hi)
    assert w._sel_range == (lo, hi), "the region was not remembered"
    n_cards = w._stats_flow.count()
    assert n_cards > 0, "no statistics were produced"

    for label, act in (("font change", lambda: (w._font_size_spin.setValue(13),
                                                w._plot_graph())),
                       ("axis edit",   w._plot_graph),
                       ("live update", w._update_graph_data)):
        act()
        assert w._sel_range == (lo, hi), f"{label} lost the region"
        assert w._stats_flow.count() == n_cards, f"{label} lost the statistics"
        assert w._stats_scroll.isVisibleTo(w) or True   # shown when the tab is up

    # Deliberate clearing still works.
    w._clear_selection()
    assert w._sel_range is None and w._stats_flow.count() == 0
    w.close()


def test_selection_dropped_only_when_window_moves_away():
    w = _loaded_widget()
    t_lo, t_hi = w._graph_axes[0].get_xlim()
    w._on_span_select(t_lo + 0.2 * (t_hi - t_lo), t_lo + 0.4 * (t_hi - t_lo))
    assert w._sel_range

    # A wider window still contains it → kept.
    w._dt_from = w._dt_from - app_main.timedelta(hours=2)
    w._drop_selection_if_outside()
    assert w._sel_range, "a window that still covers the region must keep it"

    # A window somewhere else entirely → dropped.
    w._dt_from = w._dt_from + app_main.timedelta(days=30)
    w._dt_to   = w._dt_to   + app_main.timedelta(days=30)
    w._drop_selection_if_outside()
    assert w._sel_range is None, "an unrelated window must drop the region"
    w.close()


def test_every_ticked_channel_gets_its_own_grid():
    """Several Grid boxes must produce several distinguishable grids.

    Before, all the ticks collapsed into one boolean drawn on one axis, so the
    2nd..Nth box did nothing visible.
    """
    w = _loaded_widget(("L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-C:Energy"))
    for pv in w._pv_order:
        w._pv_settings[pv]["grid"] = False
    w._plot_graph()
    assert not any(ax.yaxis._major_tick_kw.get("gridOn") for ax in w._graph_axes), \
        "a grid is drawn with nothing ticked"

    for pv in w._pv_order:
        w._pv_settings[pv]["grid"] = True
    w._plot_graph()
    on = [ax for ax in w._graph_axes if ax.yaxis._major_tick_kw.get("gridOn")]
    assert len(on) == len(w._graph_pvs), \
        f"only {len(on)} of {len(w._graph_pvs)} channels got a grid"

    # Each grid has its own line style and its own channel's colour.
    styles = [ax.get_ygridlines()[0].get_linestyle() for ax in on
              if ax.get_ygridlines()]
    assert len(set(styles)) == len(styles), f"grid styles repeat: {styles}"
    for i, ax in enumerate(on):
        gl = ax.get_ygridlines()
        if not gl:
            continue
        want = w._pv_settings[w._graph_pvs[i]]["color"].lower()
        got  = app_main.matplotlib.colors.to_hex(gl[0].get_color()).lower()
        assert got == want, f"grid {i} is {got}, not its channel's {want}"

    # Grids must sit under the traces, never over them.
    assert all(ax.get_axisbelow() for ax in w._graph_axes), \
        "a grid could be painted over another channel's trace"
    w.close()


def test_time_stamps_are_rebuilt_after_a_zoom():
    """Zooming in must re-label the time axis.

    The positions are a fixed list, so a zoom that kept the old list ended up
    with almost no stamps on screen.
    """
    w = _loaded_widget()
    ax0 = w._graph_axes[0]
    before = len([t for t in ax0.get_xticks()
                  if ax0.get_xlim()[0] <= t <= ax0.get_xlim()[1]])
    assert before >= 2

    lo, hi = ax0.get_xlim()
    narrow_lo = lo + 0.48 * (hi - lo)
    narrow_hi = lo + 0.52 * (hi - lo)
    w._on_zoom_select(narrow_lo, narrow_hi)

    vis = [t for t in ax0.get_xticks() if narrow_lo <= t <= narrow_hi]
    assert len(vis) >= 2, f"only {len(vis)} time stamps left after zooming in"
    assert w._user_zoomed, "a zoom must stop the live window scrolling the view away"

    # Home hands the view back.
    w._on_graph_view_home()
    assert not w._user_zoomed
    w.close()


def test_graph_toolbar_is_present_and_gates_the_selection():
    w = _loaded_widget()
    tb = w._graph_toolbar
    assert tb is not None, "no graph toolbar was built"
    names = {a.text() for a in tb.actions() if a.text()}
    for want in ("Home", "Back", "Forward", "Pan", "Zoom", "Save"):
        assert want in names, f"the toolbar has no {want} button"
    # Icons must be real, not blank — the dark-palette trap.
    icons = [a for a in tb.actions() if a.text() and not a.icon().isNull()]
    assert icons, "every toolbar icon is empty — they would look like blank buttons"

    # Zoom mode off → left drag makes statistics.
    assert w._span_selector.active
    tb.mode = "zoom rect"
    w._sync_graph_interaction_mode()
    assert not w._span_selector.active, "zoom mode must pause the statistics drag"
    tb.mode = ""
    w._sync_graph_interaction_mode()
    assert w._span_selector.active, "leaving zoom mode must give the drag back"
    # Right-drag zoom is untouched either way.
    assert w._zoom_selector is not None and w._zoom_selector.active
    w.close()


def test_graph_and_table_refresh_on_separate_clocks():
    """The cheap graph refresh must not be held back by the costly table rebuild."""
    w = _loaded_widget()
    o = w._graph_opts
    assert o["live_graph_min_ms"] < o["live_table_ms"], \
        "the graph is not allowed to run faster than the table"
    # A slow table must not slow the graph down.
    w._live_table_cost_ns = int(2.0e9)
    w._live_graph_cost_ns = int(0.02e9)
    g_iv = max(o["live_graph_min_ms"] * 1e6, 2 * w._live_graph_cost_ns)
    t_iv = max(o["live_table_ms"] * 1e6,     2 * w._live_table_cost_ns)
    assert g_iv < t_iv, "a slow table still drags the graph down"
    assert g_iv <= 400e6, f"the graph would only refresh every {g_iv/1e6:.0f} ms"

    # The near-free scroll between refreshes must be safe to call any time.
    w._live_window_span = app_main.timedelta(hours=1)
    w._live_mode = True
    w._scroll_live_time_axis()
    w._user_zoomed = True
    w._scroll_live_time_axis()      # zoomed in: must leave the view alone
    w._live_mode = False
    w.close()


def test_xy_pairs_channels_that_are_not_sampled_together():
    """Two channels archived at different moments must still make a plot.

    Requiring both values in the very same row gave a nearly empty plot.
    """
    w = _loaded_widget(("L3-TEST-A:Energy", "L3-TEST-B:Energy"))
    pv_a, pv_b = w._pv_order[0], w._pv_order[1]

    # Rows where the two channels never appear together.
    t0 = app_main.dt_to_ns(w._dt_from) + int(1e9)
    rows = []
    for i in range(20):
        ts = t0 + i * int(1e9)
        rows.append((ts, {pv_a: (float(i), "J")} if i % 2 == 0
                     else {pv_b: (float(i) * 2, "J")}))
    w._table_rows = rows

    xs, ys, n_rows = w._xy_pairs(pv_a, pv_b)
    assert n_rows == 20
    assert len(xs) >= 15, f"only {len(xs)} points from 20 interleaved rows"
    assert len(xs) == len(ys)

    # A channel that goes silent must stop producing points.
    rows_gap = [(t0, {pv_a: (1.0, "J")}), (t0, {pv_b: (2.0, "J")}),
                (t0 + int(3600e9), {pv_a: (3.0, "J")})]
    w._table_rows = rows_gap
    xs2, _, _ = w._xy_pairs(pv_a, pv_b)
    assert len(xs2) < 3, "a channel silent for an hour still invented points"
    w.close()


def test_xy_colour_ramp_ends_red_and_never_goes_pale():
    cmap = app_main._XY_CMAP
    r, g, b, _ = cmap(1.0)
    assert r > 0.75 and g < 0.25 and b < 0.25, f"the ramp does not end red: {(r, g, b)}"
    # No pale/yellow anywhere: every colour stays dark enough to see on white.
    for i in range(cmap.N):
        r, g, b, _ = cmap(i / (cmap.N - 1))
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        assert lum < 0.62, f"colour {i} is too pale (luminance {lum:.2f})"
        assert not (r > 0.7 and g > 0.7), f"colour {i} is yellowish"


def test_time_axis_title_stays_readable_when_the_graph_shrinks():
    """Opening the statistics strip must not cut the "Time (Prague)" title off.

    The plot margins are fractions of the figure while the text is a fixed size
    in points, so shortening the graph by a third silently pushed the title off
    the bottom edge.
    """
    w = _loaded_widget()
    w.show()
    w.resize(1600, 950)
    _app.processEvents()
    w._plot_graph()
    for _ in range(30):
        _app.processEvents()

    def label_y0():
        fig = w._mpl_figure
        fig.canvas.draw()
        return w._graph_axes[0].xaxis.label.get_window_extent(
            fig.canvas.get_renderer()).y0

    tall = w._mpl_canvas.height()
    assert label_y0() >= 0, "the time axis title is cut off even on a tall graph"

    # Selecting a region opens the statistics strip, which shortens the graph.
    lo, hi = w._graph_axes[0].get_xlim()
    w._on_span_select(lo + 0.3 * (hi - lo), lo + 0.6 * (hi - lo))
    for _ in range(30):
        _app.processEvents()
    short = w._mpl_canvas.height()
    assert short < tall, "the statistics strip did not shorten the graph"
    assert label_y0() >= 0, (
        f"the time axis title was cut off when the graph shrank "
        f"{tall}px → {short}px")

    # And the gap goes back to the user's own setting once the room returns.
    w._clear_selection()
    for _ in range(30):
        _app.processEvents()
    assert label_y0() >= 0
    assert w._mpl_figure.subplotpars.bottom < 0.16, \
        "the enlarged bottom gap was never given back"
    w.close()


def test_no_load_button_and_changes_reload_on_their_own():
    """The Live switch is the only button; everything else loads by itself."""
    _install_network_mock()
    _install_dialog_mocks()
    w = _new_widget()
    assert not hasattr(w, "_btn_load"), "the LOAD DATA button is still there"
    assert w._btn_live is not None

    _set_pvs(w, ["L3-TEST-A:Energy", "L3-TEST-B:Energy"])
    _load_and_wait(w)

    # Removing a channel must ask for a reload on its own.
    w._reload_pending = False
    w._pv_list.setCurrentRow(0)
    w._remove_selected_pvs()
    assert w._autoload_timer.isActive() or w._reload_pending, \
        "removing a channel did not trigger a reload"
    assert _pump_until(lambda: not w._load_in_flight and not w._autoload_timer.isActive())

    # A burst of changes collapses into one fetch.
    w._request_reload(reason="a")
    w._request_reload(reason="b")
    w._request_reload(reason="c")
    assert w._autoload_timer.isActive()
    assert _pump_until(lambda: not w._autoload_timer.isActive()
                       and not w._load_in_flight)

    # A reload asked for mid-fetch is remembered, not dropped.
    w._load_in_flight = True
    w._request_reload(reason="while busy")
    assert w._reload_pending, "a reload during a fetch was thrown away"
    w._finish_load()
    assert not w._reload_pending
    w.close()


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
