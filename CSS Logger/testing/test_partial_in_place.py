"""
Pictures while a load fills in: drawn into the graph on screen, not rebuilt.

Reported 16.10.2026: the graph blinked and stalled on every piece that arrived
during a load (each piece threw the canvas and the toolbar away and built new
ones), and Live start showed an empty graph until the LAST signal had answered.

What this pins:

  * a long load sends several pictures, and after the first one they go into
    the same canvas (no new canvas, no full rebuild per piece),
  * the grey "loading" area moves with the read and is gone at the end,
  * Live start draws the signals that have arrived while a slow one is still
    being read.

Runs headless with the network faked — no archiver needed.

Run:   python testing/test_partial_in_place.py    (exits non-zero on failure)
"""
import gc
import os
import pathlib
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
_APP = _HERE.parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="csslogger_test_")

import cpva_core
import main as app_main
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

_app = QApplication.instance() or QApplication([])

HOUR = int(3600e9)
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class _Fake:
    """cpva_core.cpva_fetch_samples with a delay per request (per channel)."""

    def __init__(self, delays, per_request=40):
        self.delays = delays
        self.per_request = per_request

    def install(self):
        cpva_core.cpva_fetch_samples = self
        cpva_core._DENSITY_MEMO.clear()
        return self

    def __call__(self, channel, start_ns, end_ns, timeout=10.0, count=None):
        time.sleep(self.delays.get(channel, 0.0))
        n = max(1, min(int(count or self.per_request), self.per_request))
        step = max(1, (end_ns - start_ns) // n)
        return [{"time": int(start_ns) + i * step, "value": float(i % 7),
                 "quality": "Original", "metaData": {"units": "J"}}
                for i in range(n)]


_BASE_THREADS = threading.active_count()


def _pump(seconds=0.0, cond=None):
    deadline = time.time() + seconds
    while True:
        _app.processEvents()
        if cond is not None and cond():
            return True
        if time.time() >= deadline:
            return cond() if cond is not None else True
        time.sleep(0.01)


def _new_widget(pvs):
    app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        if hasattr(app_main, fn):
            setattr(app_main, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    w = app_main.CSSLoggerWidget()
    w._pv_list.clear()
    for pv in pvs:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    if w._master_multiple_edit is not None:
        w._master_multiple_edit.setText("")
    w._conditions = []
    w.resize(1200, 800)
    return w


def _teardown(w, timeout_s=15.0):
    w._stop_live()
    w._load_epoch += 1
    w._live_epoch += 1
    deadline = time.time() + timeout_s
    while threading.active_count() > _BASE_THREADS and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.02)
    w.close()
    w.deleteLater()
    _app.processEvents()
    gc.collect()
    _app.processEvents()


def _spy(w):
    """Count full rebuilds and in-place pictures."""
    counts = {"rebuild": 0, "in_place": 0, "canvases": set()}
    real_plot = w._plot_graph
    real_in_place = w._refresh_graph_in_place

    def plot():
        counts["rebuild"] += 1
        real_plot()
        counts["canvases"].add(id(w._mpl_canvas))

    def in_place(sync=False):
        ok = real_in_place(sync=sync)
        if ok:
            counts["in_place"] += 1
            counts["canvases"].add(id(w._mpl_canvas))
        return ok

    w._plot_graph = plot
    w._refresh_graph_in_place = in_place
    return counts


def test_long_load_draws_in_place():
    pvs = ["L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-C:Energy"]
    _Fake({pv: 1.0 for pv in pvs}).install()
    w = _new_widget(pvs)
    counts = _spy(w)
    now = datetime.now()
    w._dt_from, w._dt_to = now - timedelta(days=40), now
    greys = []
    real_partial = w._draw_load_partial

    def partial(*a, **k):
        real_partial(*a, **k)
        greys.append(bool(getattr(w, "_loading_artists", None)))

    w._draw_load_partial = partial
    w._on_load_clicked(silent=True)
    ok = _pump(90.0, cond=lambda: not w._load_in_flight)
    check("the load finishes", ok)
    check("several pictures came while it loaded", len(greys) >= 2,
          f"{len(greys)} pictures")
    check("pictures after the first go into the graph on screen",
          counts["in_place"] >= 1 and counts["in_place"] >= len(greys) - 2,
          f"{counts['in_place']} in place, {counts['rebuild']} rebuilds")
    check("the grey 'loading' area was shown while loading", any(greys))
    check("no grey area once the load ended",
          not getattr(w, "_loading_artists", None))
    check("the graph has every signal", len(w._graph_lines) == len(pvs))
    _teardown(w)


def test_live_start_draws_what_has_arrived():
    pvs = ["L3-TEST-A:Energy", "L3-TEST-B:Energy", "L3-TEST-C:Energy"]
    _Fake({pvs[0]: 0.05, pvs[1]: 0.05, pvs[2]: 4.0}).install()
    w = _new_widget(pvs)
    w._live_mode = True
    w._live_window_span = timedelta(hours=1)
    w._live_initial_load()
    drawn = _pump(3.5, cond=lambda: w._load_in_flight and len(w._graph_lines) >= 2)
    check("Live start shows the signals that answered while one is still read",
          drawn, f"{len(w._graph_lines)} lines, in flight={w._load_in_flight}")
    ok = _pump(30.0, cond=lambda: not w._load_in_flight)
    check("Live start finishes", ok)
    check("then every signal is on the graph", len(w._graph_lines) == len(pvs),
          f"{len(w._graph_lines)} lines")
    _teardown(w)


if __name__ == "__main__":
    for t in (test_long_load_draws_in_place, test_live_start_draws_what_has_arrived):
        try:
            t()
        except Exception:
            import traceback
            traceback.print_exc()
            FAILURES.append(t.__name__)
    print("\nall checks passed" if not FAILURES else f"\n{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1 if FAILURES else 0)
