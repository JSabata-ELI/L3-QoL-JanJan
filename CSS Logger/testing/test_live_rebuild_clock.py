"""Live: the table (and with it every custom PV) must not fall behind.

Two faults behind "custom PVs are sometimes not there for a while":

1. The table clock was restarted on every due tick, also on one that brought
   no data — that tick skipped the rebuild AND postponed the next one, so with
   sparse channels the custom PVs (recomputed only by the rebuild) lagged by
   many seconds.
2. Every PV was filtered by the newest timestamp of ALL PVs, so a slower
   channel's sample that was archived a moment after a faster channel's newer
   one was dropped for good.

Headless, network mocked:

    python testing/test_live_rebuild_clock.py
"""
import gc
import os
import sys
import threading
import time
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox   # noqa: E402

import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []
A, B = "L3-TEST-A:Energy", "L3-TEST-B:Energy"


def check(name: str, cond: bool, detail: str = ""):
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


_BASE_THREADS = threading.active_count()


def _teardown(w):
    w._stop_live()
    deadline = time.time() + 10
    while threading.active_count() > _BASE_THREADS and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.02)
    w.close()
    w.deleteLater()
    _app.processEvents()
    gc.collect()
    _app.processEvents()


def _new_widget():
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        if hasattr(css, fn):
            setattr(css, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    w = css.CSSLoggerWidget()
    w._pv_list.clear()
    for pv in (A, B):
        w._pv_list.addItem(pv)
    w._update_pv_count()
    w._master_multiple_edit.setText("")
    w._conditions = []
    w._custom_pvs = [{"name": "Sum", "expr": "A+B", "bindings": {"A": A, "B": B}}]
    w._pv_order = [A, B]
    w._base_pv_order = [A, B]
    return w


def main() -> int:
    print("test_live_rebuild_clock")
    now = css.now_ns()

    # ── 1. a due tick with nothing new still rebuilds what earlier ticks brought
    w = _new_widget()
    w._live_mode = True
    w._live_window_span = timedelta(hours=1)
    w._samples_by_pv = {A: [(now - 60_000_000_000, 1.0, "")],
                        B: [(now - 59_000_000_000, 10.0, "")]}
    w._pre_window_vals = {}
    w._live_last_ts = now - 59_000_000_000
    # Tick with data while the table is NOT due.
    w._live_last_rebuild_ns = css.now_ns()
    w._live_table_cost_ns = 0
    t_new = now - 1_000_000_000
    w._on_incremental_finished(({A: [(t_new, 2.0, "")]}, 1, now, []))
    check("data while the table is not due is held back",
          not any(ts == t_new for ts, _r in w._table_rows_unfiltered))
    # Now the table is due, and this tick brings nothing.
    w._live_last_rebuild_ns = 0
    w._on_incremental_finished(({}, 0, css.now_ns(), []))
    check("the next due tick merges it even though it brought nothing",
          any(ts == t_new for ts, _r in w._table_rows_unfiltered),
          f"{len(w._table_rows_unfiltered)} rows")
    sums = [v for _t, v, _u in w._samples_by_pv.get("Sum", [])]
    check("and the custom PV follows", sums and sums[-1] == 12.0, f"Sum = {sums[-3:]}")
    check("the table clock restarted with that rebuild",
          w._live_last_rebuild_ns > 0)
    _teardown(w)

    # ── 2. a late sample of the slower PV is kept ─────────────────────────────
    w = _new_widget()
    w._live_mode = True
    w._live_window_span = timedelta(hours=1)
    now = css.now_ns()
    newest_a = now - 1_000_000_000          # A is the fast one
    own_b = now - 20_000_000_000            # what B had so far
    late_b = now - 3_000_000_000            # older than A's newest, newer than B's
    w._samples_by_pv = {A: [(newest_a, 1.0, "")], B: [(own_b, 10.0, "")]}
    w._live_last_ts = newest_a
    calls = []

    def _fake(channel, start_ns, end_ns, *a, **k):
        calls.append((channel, int(start_ns)))
        # The archiver answers everything in the range plus the sample before it.
        if channel == B:
            return [{"time": own_b, "value": 10.0, "metaData": {}},
                    {"time": late_b, "value": 11.0, "metaData": {}}]
        return [{"time": newest_a, "value": 1.0, "metaData": {}}]

    for mod in (cpva_core, css):
        if hasattr(mod, "cpva_fetch_samples"):
            mod.cpva_fetch_samples = _fake
    w._live_tick()
    _pump(5.0, lambda: any(t == late_b for t, _v, _u in w._samples_by_pv.get(B, [])))
    b_ts = [t for t, _v, _u in w._samples_by_pv.get(B, [])]
    check("the late sample of the slower PV is kept", late_b in b_ts, str(b_ts))
    check("nothing is added twice", len(b_ts) == len(set(b_ts)) and
          len([t for t, _v, _u in w._samples_by_pv.get(A, [])]) == 1, str(b_ts))
    check("the query reaches back before the newest sample",
          calls and min(s for _c, s in calls) <= late_b, str(calls[:2]))
    _teardown(w)

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
