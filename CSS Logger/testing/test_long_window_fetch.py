"""
Long-period loading test for the CSS Logger.

The archiver answers HTTP 500 when one response would carry too many samples.
The Logger used to send a single request per signal over the whole period, so
anything much beyond half a day lost every signal outright and came up as an
empty graph. It also skipped, on purpose and without a word, every hour lying
wholly between 22:00 and 06:00.

What this pins:

  * a period the archiver can serve in one go costs exactly one request per
    signal,
  * 12 h, 48 h and a whole year all come back covering the WHOLE period when
    the archiver refuses anything above six hours,
  * a period lying entirely inside the night returns data (the regression guard
    for the removed night filter),
  * a slow signal is not dragged down to a fast signal's request size,
  * the decimation target is shared out, not multiplied, by the splitting,
  * no two merged samples share a timestamp and the list is ascending,
  * a range that fails at every size is REPORTED, while the other signals still
    come back complete,
  * a cancelled load stops promptly and says what it never read,
  * the progress counts never go backwards and end level,
  * requests are served newest first, so a long period paints in from the
    present backwards,
  * at widget level: a three-day load paints a graph and a table, the status
    line names the unread ranges, the fetch lock is released, and the
    boundary-row filter does not eat rows that are an hour apart by nature.

Runs headless with the network faked — no archiver needed.

Run:   python testing/test_long_window_fetch.py    (exits non-zero on failure)
  or:  pytest testing/test_long_window_fetch.py
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
# Keep the real settings file out of harm's way.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="csslogger_test_")

import cpva_core
import main as app_main
from cpva_core import TZ_PRAGUE
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

_app = QApplication.instance() or QApplication([])

HOUR = int(3600e9)
DAY = 24 * HOUR
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


# ── A fake archiver that refuses anything too wide ──────────────────────────

class _Resp:
    def __init__(self, code):
        self.status_code = code


class _TooMuch(Exception):
    """Stands in for requests.HTTPError with a 500 — the archiver's "too big"."""

    def __init__(self, code=500):
        super().__init__(f"HTTP {code}")
        self.response = _Resp(code)


class _Fake:
    """Install as cpva_core.cpva_fetch_samples.

    `limits` maps a channel to the widest span it will serve; anything wider is
    refused the way the real archiver refuses it. Every answer also carries the
    sample just BEFORE the requested start, exactly as the real one does, so the
    de-duplication is under test too.
    """

    def __init__(self, limits, per_request=40, delay=0.0):
        self.limits = limits
        self.per_request = per_request
        self.delay = delay
        self.calls = []
        self._lock = threading.Lock()

    def install(self):
        cpva_core.cpva_fetch_samples = self
        return self

    def __call__(self, channel, start_ns, end_ns, timeout=10.0, count=None):
        with self._lock:
            self.calls.append((channel, int(start_ns), int(end_ns), count))
        if self.delay:
            time.sleep(self.delay)
        limit = self.limits.get(channel, HOUR)
        if end_ns - start_ns > limit:
            raise _TooMuch()
        n = max(1, min(int(count or self.per_request), self.per_request))
        step = max(1, (end_ns - start_ns) // n)
        out = [{"time": int(start_ns) - 1, "value": 0.0, "quality": "Original",
                "metaData": {"units": "J"}}]
        out += [{"time": int(start_ns) + i * step, "value": float(i),
                 "quality": "Original", "metaData": {"units": "J"}}
                for i in range(n)]
        return out

    def count_for(self, channel):
        return sum(1 for c in self.calls if c[0] == channel)


def _fetch(channels, span, limits, count=2000, **kw):
    end = 1_756_000_000_000_000_000
    start = end - span
    prog = []
    kw.setdefault("preflight", False)
    res, err, rep = cpva_core.cpva_fetch_many_adaptive(
        channels, start, end, count=count,
        progress_fn=lambda d, t: prog.append((d, t)), **kw)
    return res, err, rep, prog, start, end


# ── Fetch-level tests ───────────────────────────────────────────────────────

def test_a_servable_period_costs_one_request():
    """No hunting when the archiver simply serves the period.

    Two requests per signal, not one: the first is the small opening request at
    the newest end, which is what puts something on screen immediately. What
    must not happen is any SPLITTING.
    """
    fake = _Fake({"A": 60 * DAY, "B": 60 * DAY}).install()
    res, err, rep, _p, _s, _e = _fetch(["A", "B"], 2 * DAY, fake.limits)
    check("no request is ever refused, so nothing is split",
          rep.splits == 0 and not err, f"{rep.splits} splits, err={list(err)}")
    check("one opening request plus one for the rest, per signal",
          rep.requests == 4, f"{rep.requests} requests")
    check("both signals have data", all(res[c] for c in ("A", "B")))


def test_long_periods_cover_the_whole_period():
    for label, span in (("12 h", 12 * HOUR), ("48 h", 2 * DAY), ("1 year", 365 * DAY)):
        fake = _Fake({"A": 6 * HOUR}).install()
        res, err, rep, _p, start, end = _fetch(["A"], span, fake.limits)
        ts = [s["time"] for s in res["A"]]
        covered = cpva_core._coalesce_ranges(
            [(a, b) for _c, a, b, _n in fake.calls if b - a <= 6 * HOUR])
        check(f"{label}: nothing left unread", not rep.gaps["A"],
              str(rep.gaps["A"][:2]))
        check(f"{label}: the whole period was requested",
              len(covered) == 1 and covered[0][0] == start and covered[0][1] == end,
              f"{len(covered)} block(s)")
        check(f"{label}: samples span the period",
              bool(ts) and min(ts) <= start and max(ts) >= end - 6 * HOUR,
              f"{len(ts)} samples")


def test_night_hours_are_read():
    """A period wholly inside 22:00-06:00 used to come back empty on purpose."""
    fake = _Fake({"A": 60 * DAY}).install()
    night_start = datetime(2026, 8, 20, 23, 0, tzinfo=TZ_PRAGUE)
    start = int(night_start.timestamp() * 1e9)
    end = int((night_start + timedelta(hours=4)).timestamp() * 1e9)
    res, err, rep = cpva_core.cpva_fetch_many_adaptive(
        ["A"], start, end, count=2000, preflight=False)
    check("a night-time period returns data", bool(res["A"]) and not err,
          f"{len(res['A'])} samples, err={list(err)}")
    check("the night period was actually requested", fake.count_for("A") >= 1)


def test_a_slow_signal_is_not_dragged_down():
    fake = _Fake({"SLOW": 60 * DAY, "FAST": HOUR}).install()
    res, err, rep, _p, _s, _e = _fetch(["SLOW", "FAST"], 2 * DAY, fake.limits)
    check("the slow signal is never split (opening request + the rest)",
          fake.count_for("SLOW") == 2, f"{fake.count_for('SLOW')} requests")
    check("the fast signal is still read in full", not rep.gaps["FAST"],
          str(rep.gaps["FAST"][:2]))
    check("the fast signal did not need a request per minute",
          fake.count_for("FAST") < 2 * 24 * 4, f"{fake.count_for('FAST')} requests")


def test_the_decimation_target_is_shared_not_multiplied():
    fake = _Fake({"A": 6 * HOUR}, per_request=5000).install()
    res, _err, _rep, _p, _s, _e = _fetch(["A"], 2 * DAY, fake.limits, count=2000)
    n = len(res["A"])
    check("total points stay near the target", n <= 4 * 2000, f"{n} points")
    check("and are not starved either", n >= 200, f"{n} points")


def test_merged_samples_are_unique_and_ascending():
    fake = _Fake({"A": 6 * HOUR}).install()
    res, _err, _rep, _p, _s, _e = _fetch(["A"], 2 * DAY, fake.limits)
    ts = [s["time"] for s in res["A"]]
    check("no duplicate timestamps", len(ts) == len(set(ts)),
          f"{len(ts) - len(set(ts))} duplicates")
    check("ascending in time", ts == sorted(ts))


def test_a_hopeless_range_is_reported_not_hidden():
    fake = _Fake({"A": 60 * DAY, "DEAD": 0}).install()
    res, err, rep, _p, start, end = _fetch(["A", "DEAD"], 4 * HOUR, fake.limits)
    check("the unreadable signal is named", "DEAD" in err, str(list(err)))
    check("its unread range is stated", bool(rep.gaps["DEAD"]))
    check("the range covers the whole period",
          rep.gaps["DEAD"] and rep.gaps["DEAD"][0] == (start, end),
          str(rep.gaps["DEAD"][:2]))
    check("the other signal is unaffected", bool(res["A"]) and not rep.gaps["A"])


def test_cancelling_stops_promptly_and_says_so():
    fake = _Fake({"A": 60 * DAY}, delay=0.01).install()
    res, err, rep, _p, start, end = _fetch(
        ["A"], 30 * DAY, fake.limits, cancel_fn=lambda: True)
    check("a cancelled load makes no requests", fake.count_for("A") == 0,
          f"{fake.count_for('A')} requests")
    check("it is marked as cancelled", rep.cancelled)
    check("and the period is reported unread", bool(rep.gaps["A"]))


def test_progress_never_goes_backwards():
    fake = _Fake({"A": 6 * HOUR, "B": 3 * HOUR}).install()
    _res, _err, _rep, prog, _s, _e = _fetch(["A", "B"], 3 * DAY, fake.limits)
    mono = all(prog[i][0] <= prog[i + 1][0] and prog[i][1] <= prog[i + 1][1]
               for i in range(len(prog) - 1))
    check("done and total never fall", mono)
    check("done never exceeds total", all(d <= t for d, t in prog))
    check("the last report is level", bool(prog) and prog[-1][0] == prog[-1][1],
          str(prog[-1] if prog else None))


def test_newest_is_read_first():
    fake = _Fake({"A": 60 * DAY}).install()
    seen = []
    end = 1_756_000_000_000_000_000
    cpva_core.cpva_fetch_many_adaptive(
        ["A"], end - 10 * DAY, end, count=None, max_workers=1, preflight=False,
        chunk_fn=lambda ch, s: seen.append(s[1]["time"]))
    check("the newest part of the period arrives first",
          len(seen) > 2 and seen[0] > seen[-1],
          f"{len(seen)} chunks, first={seen[0] if seen else None}")


# ── Widget-level test ───────────────────────────────────────────────────────

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
    return w


def _teardown(w, timeout_s=15.0):
    w._stop_live()
    w._load_epoch += 1
    deadline = time.time() + timeout_s
    while threading.active_count() > _BASE_THREADS and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.02)
    w.close()
    w.deleteLater()
    _app.processEvents()
    gc.collect()
    _app.processEvents()


def test_three_day_load_in_the_widget():
    pvs = ["L3-TEST-A:Energy", "L3-TEST-B:Energy"]
    fake = _Fake({pvs[0]: HOUR, pvs[1]: HOUR}).install()
    w = _new_widget(pvs)
    now = datetime.now()
    w._dt_from, w._dt_to = now - timedelta(days=3), now
    w._on_load_clicked(silent=True)
    ok = _pump(60.0, cond=lambda: not w._load_in_flight)
    check("a three-day load finishes", ok)
    check("the fetch lock is released", not w._load_in_flight)
    check("a graph was drawn", w._mpl_canvas is not None)
    check("the table has rows", len(w._table_rows) > 0, f"{len(w._table_rows)} rows")
    check("every signal got data",
          all(w._samples_by_pv.get(p) for p in pvs),
          {p: len(w._samples_by_pv.get(p, [])) for p in pvs})
    check("the status line does not claim an empty archive",
          "Nothing could be read" not in w._lbl_status.text(),
          w._lbl_status.text())
    _teardown(w)


def test_boundary_filter_keeps_naturally_hourly_rows():
    """Thinned data sits about an hour apart by nature. The seam filter must
    only look at the request boundaries the fetch actually used, or it deletes
    almost every row of a long period."""
    w = _new_widget(["P1", "P2"])
    t0 = 1_756_000_000_000_000_000
    rows = []
    for i in range(6):
        ts = t0 + i * HOUR
        rows.append((ts, {"P1": (1.0, ""), "P2": (2.0, "")}))
    kept_no_edges = w._remove_fake_hour_boundary_rows(rows, boundaries={"P1": []})
    check("with no real boundaries every row survives",
          len(kept_no_edges) == len(rows), f"{len(kept_no_edges)}/{len(rows)}")
    edges = {"P1": [t0 + 3 * HOUR]}
    kept_one_edge = w._remove_fake_hour_boundary_rows(rows, boundaries=edges)
    check("only the row on a real boundary is dropped",
          len(kept_one_edge) == len(rows) - 1, f"{len(kept_one_edge)}/{len(rows)}")
    kept_legacy = w._remove_fake_hour_boundary_rows(rows)
    check("with no boundary list at all the old rule still applies",
          len(kept_legacy) < len(rows), f"{len(kept_legacy)}/{len(rows)}")
    _teardown(w)


def test_the_time_window_dialog_reports_now():
    w = _new_widget(["P1"])
    now = datetime.now()
    dlg = app_main.TimeWindowDialog(w, now - timedelta(hours=1), now)
    # Built with both sides on the Relative tab; the End side starts at all
    # zeroes, which is "now".
    check("a relative end of zero is 'now'", dlg.ends_at_now())
    dlg._sides["to"]["rel"]["Hours"].setValue(3)
    check("an end three hours ago is not 'now'", not dlg.ends_at_now())
    dlg._sides["to"]["rel"]["Hours"].setValue(0)
    dlg._sides["to"]["tabs"].setCurrentIndex(0)
    check("an absolute end is never 'now'", not dlg.ends_at_now())
    dlg.deleteLater()
    _teardown(w)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    for fn in TESTS:
        print(f"\n── {fn.__name__} ──")
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__, False, f"raised {exc}")
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  {f}")
        return 1
    print("all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
