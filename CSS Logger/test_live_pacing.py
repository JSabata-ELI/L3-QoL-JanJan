"""
Live-mode pacing test for the CSS Logger.

Guards the four things that made Live saturate the archiver and the GUI thread:

  * a wide saved time window is clamped before it becomes the live window,
  * one live tick never asks for more than one chunk per PV, even when the
    archiver has been quiet for hours (`_live_last_ts` frozen in the past),
  * a tick that brings nothing still advances `_live_last_ts`, so the requested
    range cannot grow tick after tick,
  * stopping Live cancels the fetch that is already in flight,
  * the table reuses its items instead of reallocating the whole grid.

Runs headless with the network mocked — no archiver needed.

Run:   python test_live_pacing.py     (prints PASS/FAIL, exits non-zero on failure)
  or:  pytest test_live_pacing.py
"""
import gc
import os
import sys
import pathlib
import threading
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = pathlib.Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import cpva_core
import main as app_main
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

_app = QApplication.instance() or QApplication([])

_PVS = ["L3-TEST-A:Energy", "L3-TEST-B:Energy"]


# ── Harness ──────────────────────────────────────────────────────────────────

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


def _teardown(w, timeout_s=10.0):
    """Stop Live, wait for the fetch threads, then destroy the widget.

    Two shutdown hazards, both harness-only but both crash the interpreter
    (0xC0000005) instead of failing a test:
      * a fetch worker holds the signal object it emits into, so the widget must
        outlive its threads;
      * the widget ↔ signal-object reference cycle keeps a closed widget alive
        until the cycle collector runs, and if that only happens at interpreter
        exit, Qt is already tearing itself down. Collect explicitly, while the
        QApplication is still there.
    """
    w._stop_live()
    deadline = time.time() + timeout_s
    while threading.active_count() > _BASE_THREADS and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.02)
    w.close()
    w.deleteLater()
    _app.processEvents()
    gc.collect()
    _app.processEvents()


def _new_widget():
    app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        if hasattr(app_main, fn):
            setattr(app_main, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    w = app_main.CSSLoggerWidget()
    w._pv_list.clear()
    for pv in _PVS:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    if w._master_multiple_edit is not None:
        w._master_multiple_edit.setText("")
    w._conditions = []
    return w


class _Recorder:
    """Stands in for cpva_fetch_samples and records every requested range."""

    def __init__(self, delay_s=0.0, n=5):
        self.calls = []          # (channel, start_ns, end_ns)
        self.delay_s = delay_s
        self.n = n
        self.started = 0

    def __call__(self, channel, start_ns, end_ns, *args, **kwargs):
        self.started += 1
        self.calls.append((channel, int(start_ns), int(end_ns)))
        if self.delay_s:
            time.sleep(self.delay_s)
        span = max(int(end_ns) - int(start_ns), 1)
        return [{"time": int(start_ns) + span * i // self.n,
                 "value": 100.0 + i, "metaData": {"units": "J"}}
                for i in range(self.n)]

    def install(self):
        for mod in (cpva_core, app_main):
            if hasattr(mod, "cpva_fetch_samples"):
                mod.cpva_fetch_samples = self
            if hasattr(mod, "cpva_fetch_samples_chunked"):
                mod.cpva_fetch_samples_chunked = self
        return self


# ── Tests ────────────────────────────────────────────────────────────────────

def test_live_uses_the_window_it_is_given():
    """Live must show the period on screen, however wide, and never a shortened
    one. It used to clamp anything over 12 h, so the graph quietly disagreed
    with the From/To it was captioned with."""
    _Recorder().install()
    w = _new_widget()
    now = datetime.now()
    w._dt_from, w._dt_to = now - timedelta(hours=49), now
    assert w._live_span_from_window() == timedelta(hours=49)
    w._dt_from, w._dt_to = now - timedelta(days=200), now
    assert w._live_span_from_window() == timedelta(days=200)
    # A sane window is used as-is, a nonsensical one falls back to an hour.
    w._dt_from, w._dt_to = now - timedelta(hours=2), now
    assert w._live_span_from_window() == timedelta(hours=2)
    w._dt_from, w._dt_to = now, now
    assert w._live_span_from_window() == timedelta(hours=1)
    _teardown(w)


def test_tick_lookback_is_bounded_when_archiver_is_quiet():
    """A frozen _live_last_ts must not widen the tick's query range."""
    rec = _Recorder().install()
    w = _new_widget()
    w._live_mode = True
    w._live_window_span = timedelta(hours=12)
    # Pretend the newest sample is 10 hours old (idle archiver / weekend).
    w._live_last_ts = app_main.now_ns() - int(10 * 3600e9)
    rec.calls.clear()
    w._live_tick()
    assert _pump(5.0, lambda: len(rec.calls) >= len(_PVS)), "tick never fetched"
    _pump(0.2)

    per_pv = {}
    for ch, s, e in rec.calls:
        per_pv.setdefault(ch, []).append((s, e))
    # One request per PV — no 1-hour-chunk fan-out.
    assert set(per_pv) == set(_PVS), per_pv
    for ch, ranges in per_pv.items():
        assert len(ranges) == 1, f"{ch} fanned out into {len(ranges)} chunks"
        s, e = ranges[0]
        span_ns = e - s
        assert span_ns <= app_main._LIVE_TICK_LOOKBACK_NS, span_ns
        assert span_ns <= cpva_core.CHUNK_SIZE_NS, span_ns
    _teardown(w)


def test_empty_tick_advances_the_cursor():
    """With no new data the cursor still moves, so the range cannot grow."""
    _Recorder().install()
    w = _new_widget()
    w._live_mode = True
    w._live_window_span = timedelta(hours=1)
    stale = app_main.now_ns() - int(5 * 3600e9)
    w._live_last_ts = stale
    end_ns = app_main.now_ns()
    w._on_incremental_finished(({}, 0, end_ns, []))
    assert w._live_last_ts > stale, "cursor stayed pinned to the stale sample"
    assert w._live_last_ts <= end_ns
    # The overlap that covers archiver ingestion lag is kept.
    assert end_ns - w._live_last_ts == app_main._LIVE_TICK_OVERLAP_NS
    _teardown(w)


def test_stop_live_cancels_the_running_fetch():
    """Stop must abandon the queued requests, not just the next timer tick."""
    rec = _Recorder(delay_s=0.05, n=3).install()
    w = _new_widget()
    w._pv_list.clear()
    for i in range(24):                     # enough tasks to queue behind the pool
        w._pv_list.addItem(f"L3-TEST-{i:02d}:Energy")
    w._update_pv_count()
    w._live_mode = True
    w._live_window_span = timedelta(hours=1)
    w._live_last_ts = None
    rec.calls.clear()
    w._live_tick()
    assert _pump(5.0, lambda: rec.started > 0), "fetch never started"
    w._stop_live()
    started_at_stop = rec.started
    _pump(1.5)
    # A few in-flight requests still finish; the queue behind them must not.
    assert rec.started - started_at_stop <= 16, (
        f"{rec.started - started_at_stop} requests ran after Stop Live")
    assert rec.started < 24, f"whole queue ran anyway ({rec.started})"
    _teardown(w)


def test_table_reuses_its_items():
    """A refresh must not reallocate the grid when the columns are unchanged."""
    _Recorder(n=20).install()
    w = _new_widget()
    w._on_load_clicked()
    assert _pump(15.0, lambda: (not w._load_in_flight) and w._table_rows)
    assert w._table_widget.rowCount() > 0
    first = w._table_widget.item(0, 0)
    w._populate_table()
    assert w._table_widget.item(0, 0) is first, "item was re-created"
    # Changing the PV set does rebuild the grid.
    w._pv_order = w._pv_order[:1]
    w._populate_table()
    assert w._table_widget.columnCount() == 2
    _teardown(w)


# ── Runner ───────────────────────────────────────────────────────────────────

def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            import traceback
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
