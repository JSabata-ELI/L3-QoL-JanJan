"""The Live switch replaces the old Archive / Live mode buttons.

What has to hold:
  * switching Live on loads the window picked in the calendar — its From time
    moved onto today — the way Image Slider does it, and falls back to the
    current whole hour when nothing has been picked;
  * switching it off hands that stretch to the archive loader once, and only
    once;
  * the calendar always wins — picking days leaves live and drops the window
    live was standing on;
  * Stop never starts a fetch of its own.

No archive is read: _start_live and _load_day_energy are replaced by counters.

    python testing/test_live_switch.py
"""
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PySide6.QtWidgets import QApplication          # noqa: E402

import sp_t                                         # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def _instrument(w):
    """Count the calls instead of making them."""
    w._calls = {"start": 0, "load": 0, "start_ns": None}

    def _start(start_ns=None):
        w._live = True
        w._calls["start"] += 1
        w._calls["start_ns"] = start_ns
        w._btn_live.setChecked(True)
        w._refresh_pill()

    def _load():
        w._calls["load"] += 1

    w._start_live = _start
    w._load_day_energy = _load
    # The >2 h question is a modal box; answer "yes" without showing it.
    w._confirm_live_preload = lambda a, b: True


def main():
    app = QApplication.instance() or QApplication([])
    w = sp_t.SpectraWidget()
    _instrument(w)

    # ── Live on ───────────────────────────────────────────────────────
    w._btn_live.setChecked(True)
    w._on_live_clicked()
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    check("Live on starts streaming", w._calls["start"] == 1)
    check("Live on reads nothing from the archive", w._calls["load"] == 0)
    check("one window is loaded", len(w._windows) == 1, str(w._windows))
    t0, t1 = w._windows[0]
    # Nothing was picked, so rule 7 falls back to the current whole hour: the
    # window starts at the top of this hour and runs to now.
    start = sp_t._ns_to_dt(t0)
    check("nothing picked starts at the top of this hour",
          (start.minute, start.second) == (0, 0), start.strftime("%H:%M:%S"))
    check("it ends now", abs(t1 - now_ns) < 5e9, f"{(now_ns - t1) / 1e9:.1f} s ago")
    check("the preload start is handed to _start_live",
          w._calls["start_ns"] == t0, str(w._calls["start_ns"]))
    check("the pick survives for the calendar to reopen on",
          len(w._segments) == 1 and sp_t.seg_fields(w._segments[0])[1] == start.hour,
          str(w._segments))
    check("the search graph is hidden", not w._top_container.isVisibleTo(w))
    check("Average last N is on screen", w._g_live.isVisibleTo(w._sidebar_scroll.widget()))

    # ── Live off ──────────────────────────────────────────────────────
    w._btn_live.setChecked(False)
    w._on_live_clicked()
    check("Live off stops streaming", not w._live)
    check("Live off reads that stretch once", w._calls["load"] == 1)
    check("the button is unchecked", not w._btn_live.isChecked())
    check("red again", "#f9dedb" in w._btn_live.styleSheet())
    check("Average last N is gone", not w._g_live.isVisibleTo(w._sidebar_scroll.widget()))

    # a second Live off must not fetch again
    w._on_live_clicked()
    check("a second Live off reads nothing", w._calls["load"] == 1)

    # ── Stop while live: no fetch of its own ──────────────────────────
    w._calls["load"] = 0
    w._btn_live.setChecked(True)
    w._on_live_clicked()
    w.cancel_scan()
    check("Stop stops live", not w._live)
    check("Stop reads nothing", w._calls["load"] == 0, str(w._calls))
    check("Stop leaves the switch red", "#f9dedb" in w._btn_live.styleSheet())

    # ── The calendar wins ─────────────────────────────────────────────
    w._calls["load"] = 0
    w._cancel.clear()
    w._btn_live.setChecked(True)
    w._on_live_clicked()
    live_window = list(w._windows)
    w._archive_reload_pending = False          # what _pick_day does
    w._stop_live()
    check("picking days drops the live window", w._calls["load"] == 0)
    check("the window is still there for the picker to overwrite",
          list(w._windows) == live_window)

    # ── A picked From is kept, only the date moves to today ───────────
    from datetime import date, timedelta
    yesterday = date.today() - timedelta(days=1)
    w._segments = [sp_t.PickSeg(yesterday, 7, 30, 21, 0)]
    w._windows = [sp_t.seg_bounds_ns(w._segments[0])]
    w._cancel.clear()
    w._btn_live.setChecked(True)
    w._on_live_clicked()
    start = sp_t._ns_to_dt(w._windows[0][0])
    check("the picked From is kept", (start.hour, start.minute) == (7, 30),
          start.strftime("%H:%M"))
    check("the date moved to today", start.date() == date.today(),
          str(start.date()))
    check("To is ignored — the window runs to now",
          abs(w._windows[0][1] - int(datetime.now(timezone.utc).timestamp() * 1e9))
          < 5e9)
    w._stop_live()

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
