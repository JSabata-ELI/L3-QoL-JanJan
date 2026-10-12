"""Spectra tab — three things that broke together:

  PV TABLE WIDTH
    * the "Search by" table's Channel column must end exactly at the table's
      right-hand edge: no horizontal scrollbar, no grey strip — with a short list
      and with a list long enough to bring up the vertical scrollbar. Before, the
      columns were fitted while the viewport still had Qt's default width, so
      Channel came out far wider than the panel.

  LIVE SURVIVES AN X AXIS CHANGE
    * changing the X axis while Live runs keeps Live on, keeps the buffered
      shots, and reads the new axis.

  A NEW WINDOW WHILE LIVE
    * a tick asked by the previous live session (old window) is dropped when it
      lands after the restart — it must not add old-window shots or move the
      poll clock.
    * declining the "This will take a while" question while live leaves the
      running session exactly as it was.

No archiver, no network. Rendered with WA_DontShowOnScreen: nothing appears.

Run:  python testing/test_spectra_live_window_and_pv_table.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from collections import deque

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

import sp_t
from sp_t import SpectraWidget

NX = 64
X = np.linspace(780.0, 840.0, NX)
SEC = 10 ** 9
T0 = int(1_756_000_000 * 1e9)

_failed = False
_KEEP: list = []


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


def _widget(shown: bool = False):
    w = SpectraWidget()
    w.resize(1500, 900)
    w._x_axis_cfg = {"mode": "native"}
    w._x_data = X
    w._chk_autofit.setChecked(False)
    if shown:
        w.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        w.show()
        for _ in range(5):
            QApplication.processEvents()
    _KEEP.append(w)
    return w


def _pump():
    for _ in range(5):
        QApplication.processEvents()


# ── PV table ──────────────────────────────────────────────────────────────────
def _check_table(w, what):
    tbl = w._tbl_pvs
    vp = tbl.viewport().width()
    cols = [tbl.columnWidth(c) for c in range(3)]
    total = sum(cols)
    hbar = tbl.horizontalScrollBar()
    _ok(total == vp, f"{what}: the three columns fill the table exactly",
        f"columns {cols} = {total}, viewport {vp}")
    _ok(hbar.maximum() == 0, f"{what}: no horizontal scrolling",
        f"scroll range 0..{hbar.maximum()}")
    _ok(tbl.width() <= w._sidebar_scroll.width(),
        f"{what}: the table sits inside the panel",
        f"table {tbl.width()}, panel {w._sidebar_scroll.width()}")


def test_pv_table_fits():
    print("\ntest_pv_table_fits")
    w = _widget(shown=True)
    w._search_pvs = [("Energy", "L3-SBW4-ENERGY:EnergyLongNameForTesting_RB"),
                     ("GDD", "L3-SPFE-AOD03-002:Order2_RB")]
    w._refresh_pv_table()
    _pump()
    _check_table(w, "2 PVs")
    w._search_pvs = [(f"PV {i}", f"L3-SOMESYSTEM-DEV{i:02d}:AVeryLongChannelName_{i}")
                     for i in range(12)]
    w._refresh_pv_table()
    _pump()
    _ok(w._tbl_pvs.verticalScrollBar().isVisible(),
        "12 PVs bring up the vertical scrollbar")
    _check_table(w, "12 PVs")
    w._cancel.set()


# ── live ──────────────────────────────────────────────────────────────────────
def _live_widget():
    w = _widget()
    # Pretend a live session is running: no archive, no timer firing for real.
    w._live = True
    w._live_gen += 1
    w._live_buf = deque(maxlen=sp_t.LIVE_BUF_MAX)
    w._live_start_ns = T0
    w._live_last_ns = T0
    w._live_scalars = {}
    w._live_arming = []
    return w


def test_x_axis_change_keeps_live():
    print("\ntest_x_axis_change_keeps_live")
    w = _live_widget()
    w._live_buf.append((T0 + SEC, np.ones(NX)))
    new_x = np.linspace(500.0, 600.0, NX)

    class _Dlg:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def result_cfg(self):
            return {"mode": "index"}

    orig_dlg = sp_t.XAxisSourceDialog
    orig_resolve = w._resolve_x_data
    orig_bg = sp_t._bg
    sp_t.XAxisSourceDialog = _Dlg
    w._resolve_x_data = lambda t0, t1: new_x
    sp_t._bg = lambda fn: fn()          # run the background read inline
    stopped = []
    orig_stop = w._stop_live
    w._stop_live = lambda: stopped.append(1) or orig_stop()
    try:
        w._open_x_axis_settings()
        _pump()
    finally:
        sp_t.XAxisSourceDialog = orig_dlg
        sp_t._bg = orig_bg
        w._resolve_x_data = orig_resolve
    _ok(w._live, "live is still running after the X axis change")
    _ok(not stopped, "live was never stopped on the way")
    _ok(len(w._live_buf) == 1, "the buffered shots are kept", f"{len(w._live_buf)}")
    _ok(w._x_data is not None and np.allclose(w._x_data, new_x),
        "the new X axis was read")
    _ok(w._x_axis_cfg.get("mode") == "index", "the new X axis setting is in use")
    w._live = False
    w._live_timer.stop()


def test_stale_tick_is_dropped():
    print("\ntest_stale_tick_is_dropped")
    w = _live_widget()
    old_gen = w._live_gen
    w._live_gen += 1                 # the window was re-picked: a new session
    new_start = T0 + 3600 * SEC
    w._live_last_ns = new_start
    old_shot = T0 + 5 * SEC
    w._on_live_y((T0 + 10 * SEC, [(old_shot, np.ones(NX))], {}), old_gen)
    _ok(len(w._live_buf) == 0, "the old window's shot is not buffered",
        f"{len(w._live_buf)} in buffer")
    _ok(w._live_last_ns == new_start, "the poll clock is not moved by it")
    _ok(not w._live_timer.isActive(), "and it does not restart the timer")
    w._on_live_y((new_start + 5 * SEC, [(new_start + 2 * SEC, np.ones(NX))], {}),
                 w._live_gen)
    _ok(len(w._live_buf) == 1, "a tick of the current session still lands")
    w._live = False
    w._live_timer.stop()


def test_declined_preload_keeps_live():
    print("\ntest_declined_preload_keeps_live")
    w = _live_widget()
    w._windows = [(T0, T0 + 600 * SEC)]
    seg = sp_t.PickSeg(sp_t._ns_to_dt(T0).date(), 10, 0, 23, 59)
    w._segments = [seg]
    gen = w._live_gen
    new_windows = [(T0 - 86_400 * SEC, T0)]

    class _Pick:
        def __init__(self, *a, **k):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def is_online_mode(self):
            return True

        def selected_windows(self):
            return list(new_windows)

        def all_segments(self):
            return [sp_t.PickSeg(sp_t._ns_to_dt(T0).date(), 0, 0, 23, 59)]

    orig_pick = sp_t.DayTimePicker
    sp_t.DayTimePicker = _Pick
    w._confirm_live_preload = lambda a, b: False
    try:
        w._pick_day()
    finally:
        sp_t.DayTimePicker = orig_pick
    _ok(w._live, "live is still running")
    _ok(w._live_gen == gen, "the same live session")
    _ok(w._windows == [(T0, T0 + 600 * SEC)], "the window is unchanged")
    _ok(w._segments == [seg], "the calendar pick is unchanged")
    w._live = False
    w._live_timer.stop()


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    test_pv_table_fits()
    test_x_axis_change_keeps_live()
    test_stale_tick_is_dropped()
    test_declined_preload_keeps_live()
    print("\nRESULT:", "FAIL" if _failed else "all passed")
    sys.exit(1 if _failed else 0)
