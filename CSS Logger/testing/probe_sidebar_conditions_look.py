"""Probe: what the sidebar, the Conditions window and the PV Time Plot look
like after the October 2026 changes — rendered with the real Windows backend
(offscreen has no fonts and lies about text size), shown WITHOUT taking the
keyboard focus.

Saves into testing/_out/:
  sidebar_short.png / sidebar_long.png  — INFO with a short and a very long
                                          message (the sidebar must not move)
  conditions_dialog.png                 — the Conditions window, every kind
  pv_time_daily.png / pv_time_raw.png   — synthetic days, upright stamps

    python testing/probe_sidebar_conditions_look.py
"""
import os
import sys
from datetime import date, datetime, timedelta

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402
from PySide6.QtCore import Qt                         # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

import main as css                                    # noqa: E402
import test_smoke as smoke                            # noqa: E402

css.save_config = lambda *a, **k: None                # never touch the real settings
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")


def quiet(win):
    """Shown, but never given the keyboard focus."""
    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    win.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)


def pump(n=6):
    for _ in range(n):
        QApplication.processEvents()


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    css.install_app_look(app)
    pvs = ("HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy", "L3-TEST-B:Energy",
           "L3-TEST-C:Energy", "L3-TEST-D:Energy")
    w = smoke._loaded_widget(pvs)
    w._set_pv_display_name(pvs[1], "Beam B (renamed)")
    quiet(w)
    w.resize(1500, 950)
    w.move(40, 40)
    w.show()
    pump()

    side = w._sidebar_widget.parentWidget().parentWidget()      # the scroll area
    w._lbl_status.setText("Ready.")
    pump()
    x0 = [b.geometry().x() for b in (w._btn_live, w._btn_browse)]
    side.grab().save(os.path.join(OUT, "sidebar_short.png"))
    w._lbl_status.setText(
        "Loaded 12 PVs · " + ", ".join([pvs[0]] * 5) +
        " · ⚠ 3 signals incomplete (2.5 h unread) · raw data — the archiver "
        "does not thin it out")
    w._lbl_cpv_info.setText("Sum of Green: F is not bound to a PV "
                            "(L3-OM1FF-CAM-EXTRA_LONG_NAME_FOR_TEST:Centroid_X)")
    pump()
    x1 = [b.geometry().x() for b in (w._btn_live, w._btn_browse)]
    side.grab().save(os.path.join(OUT, "sidebar_long.png"))
    print("sidebar width:", side.width(), "inner:", w._sidebar_widget.width(),
          "| buttons moved:", x0 != x1)
    print("status tooltip present:", bool(w._lbl_status.toolTip()))

    # ── the Conditions window ───────────────────────────────────────────────
    choices = [(w._col_label(p), p) for p in pvs]
    conds = [{"pv": pvs[0], "kind": "between", "a": 1.0, "b": 5.0},
             {"pv": pvs[1], "kind": "pct", "a": 70.0, "b": 10.0},
             {"pv": pvs[2], "kind": "abs", "a": 12.0, "b": 0.5, "enabled": False},
             {"pv": pvs[3], "kind": "above", "a": 3.0}]
    dlg = css._ConditionsDialog(conds, choices, w, count_fn=w._condition_count)
    quiet(dlg)
    dlg.show(); pump()
    dlg.editor._rows[3]["a"].setText("3x")          # a bad box shows red
    pump()
    dlg.grab().save(os.path.join(OUT, "conditions_dialog.png"))
    dlg.hide()

    # ── PV Time Plot, from synthetic cached days ────────────────────────────
    days = []
    d = date.today()
    while len(days) < 9:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.sort()
    rng = np.random.default_rng(1)
    for day in days:
        t0 = int(datetime(day.year, day.month, day.day, 8).timestamp() * 1e9)
        ts = t0 + np.arange(400, dtype=np.int64) * 60_000_000_000
        for k, pv in enumerate(pvs):
            w._pv_time_cache[(day, pv)] = {
                "ts": ts, "val": rng.normal(10 + 5 * k, 1 + k, ts.size), "seed": np.nan}
    w._pv_time_days = days
    w._refresh_pv_time_days_label()
    w._refresh_pv_time_choices()
    w._pv_time_cond_editor.add_row({"pv": pvs[1], "kind": "pct", "a": 15.0, "b": 20.0})
    w._notebook.setCurrentWidget(w._tab_pv_time)
    pump()
    w._pv_time_auto_timer.stop()
    w._plot_pv_time()
    pump(10)
    w._tab_pv_time.grab().save(os.path.join(OUT, "pv_time_daily.png"))
    w._pv_time_mode_combo.setCurrentText("Raw shots")
    w._pv_time_auto_timer.stop()
    w._plot_pv_time()
    pump(10)
    w._tab_pv_time.grab().save(os.path.join(OUT, "pv_time_raw.png"))
    print("pv time info:", w._lbl_pv_time_info.text())

    w._notebook.setCurrentIndex(0)
    pump()
    w.grab().save(os.path.join(OUT, "window_graph.png"))
    w.hide()
    print("saved to", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
