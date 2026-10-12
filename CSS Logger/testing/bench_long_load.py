"""
Bench: one long load against the REAL archiver, the way the program does it.

Prints a timeline (every partial picture, what it cost, when the load ended)
and saves a picture of the graph after every partial redraw and at the end to
testing/_out/long_load_*.png — so "do the curves appear while it loads?" is
answered by looking, not by guessing.

Nothing appears on screen (minimal platform with real fonts). The day cache
goes to a temporary folder on C: unless CPVA_CACHE_DIR is set — never the
shared one. Run it twice with the same CPVA_CACHE_DIR to time a cached load.

Run:  python testing/bench_long_load.py [days=90] [preset=Operation]
"""
import os
import pathlib
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
os.environ.setdefault("CPVA_CACHE_DIR", tempfile.mkdtemp(prefix="cpva_cache_bench_"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
_APP = _HERE.parent
sys.path.insert(0, str(_APP))
os.chdir(_APP)
OUT = _HERE / "_out"
OUT.mkdir(exist_ok=True)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import main as M

DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
PRESET = sys.argv[2] if len(sys.argv) > 2 else "Operation"

M.CSSLoggerWidget._maybe_autostart_live = lambda self: None
M.CSSLoggerWidget._save_runtime_state = lambda self, *a, **k: None
for fn in ("save_config", "save_presets", "save_condition_presets", "save_custom_pvs"):
    if hasattr(M, fn):
        setattr(M, fn, lambda *a, **k: None)

app = QApplication.instance() or QApplication(sys.argv)
w = M.CSSLoggerWidget()
w.resize(1800, 1000)
t0 = [time.monotonic()]
shots = [0]

_orig_partial = M.CSSLoggerWidget._draw_load_partial


def _partial(self, *a, **k):
    s = time.monotonic()
    try:
        return _orig_partial(self, *a, **k)
    finally:
        shots[0] += 1
        edge = getattr(self, "_loading_from_ns", None)
        edge_s = (datetime.fromtimestamp(edge / 1e9).strftime("%d.%m %H:%M")
                  if edge else "-")
        print(f"{time.monotonic() - t0[0]:7.1f}s partial #{shots[0]} "
              f"took {time.monotonic() - s:.2f}s, read back to {edge_s}", flush=True)
        if os.environ.get("BENCH_DETAIL"):
            for pv, smp in self._samples_by_pv.items():
                first = (datetime.fromtimestamp(smp[0][0] / 1e9).strftime("%d.%m %H:%M")
                         if smp else "-")
                print(f"           {pv[:40]:40s} {len(smp):7d} from {first}")
            rows = self._table_rows
            print(f"           rows {len(rows)} unfiltered "
                  f"{len(self._table_rows_unfiltered)} first row "
                  f"{datetime.fromtimestamp(rows[0][0] / 1e9) if rows else '-'}")
        _grab(f"long_load_partial_{shots[0]:02d}.png")


M.CSSLoggerWidget._draw_load_partial = _partial

_orig_finish = M.CSSLoggerWidget._CSSLoggerWidget__on_load_finished_inner


def _finish(self, *a, **k):
    """The end of the load, on the window's thread: timed, and profiled when
    BENCH_PROFILE is set."""
    s = time.monotonic()
    if os.environ.get("BENCH_PROFILE"):
        import cProfile
        import pstats
        prof = cProfile.Profile()
        prof.enable()
        try:
            return _orig_finish(self, *a, **k)
        finally:
            prof.disable()
            pstats.Stats(prof).sort_stats("cumulative").print_stats(30)
            print(f"finish took {time.monotonic() - s:.2f}s", flush=True)
    try:
        return _orig_finish(self, *a, **k)
    finally:
        print(f"{time.monotonic() - t0[0]:7.1f}s the end of the load took "
              f"{time.monotonic() - s:.2f}s on the window's thread", flush=True)


M.CSSLoggerWidget._CSSLoggerWidget__on_load_finished_inner = _finish


def _grab(name):
    canvas = getattr(w, "_mpl_canvas", None)
    if canvas is None:
        return
    try:
        canvas.draw()
        canvas.grab().save(str(OUT / name))
    except Exception as exc:
        print("grab failed:", exc)


def go():
    idx = w._preset_combo.findText(PRESET)
    if idx >= 0:
        w._preset_combo.setCurrentIndex(idx)
        w._load_preset()
    w._autoload_timer.stop()
    now = datetime.now(timezone.utc).astimezone(M.TZ_PRAGUE)
    w._dt_from = now - timedelta(days=DAYS)
    w._dt_to = now
    print(f"{len(w._real_pv_names())} signals, {DAYS:g} days, cache "
          f"{os.environ.get('CPVA_CACHE_DIR')}", flush=True)
    t0[0] = time.monotonic()
    w._on_load_clicked(silent=True)
    QTimer.singleShot(200, check)


def check():
    if w._load_in_flight and time.monotonic() - t0[0] < 900:
        QTimer.singleShot(200, check)
        return
    print(f"{time.monotonic() - t0[0]:7.1f}s END  rows={len(w._table_rows)}  "
          f"status: {w._lbl_status.text()}", flush=True)
    rep = getattr(w, "_last_fetch_report", None)
    if rep is not None:
        print(f"         requests={rep.requests} from cache={rep.cached} "
              f"fetch {rep.elapsed_s:.1f}s", flush=True)
    _grab("long_load_final.png")
    import threading
    print("         threads still running:",
          sorted(t.name for t in threading.enumerate()), flush=True)
    s = time.monotonic()
    w._plot_graph()
    print(f"         a redraw with nothing else running: {time.monotonic() - s:.2f}s",
          flush=True)
    # Let the cache writer finish before the process ends.
    QTimer.singleShot(3000, app.quit)


QTimer.singleShot(1500, go)
app.exec()
