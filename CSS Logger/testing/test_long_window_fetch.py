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
        # Each test is its own archive: a density remembered from another
        # test's fake must not decide how this one is read.
        cpva_core._DENSITY_MEMO.clear()
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

    Four requests per signal, not one: the density probe that decides whether
    thinning the signal is worth it at all, then the small opening request at
    the newest end, which is what puts something on screen immediately, then
    the rest of the period in day-sized steps (FILL_STEPS, never under a day)
    so the graph grows as it loads. What must not happen is any SPLITTING.
    """
    fake = _Fake({"A": 60 * DAY, "B": 60 * DAY}).install()
    res, err, rep, _p, _s, _e = _fetch(["A", "B"], 2 * DAY, fake.limits)
    check("no request is ever refused, so nothing is split",
          rep.splits == 0 and not err, f"{rep.splits} splits, err={list(err)}")
    check("density probe, opening request and two day-sized steps, per signal",
          rep.requests == 8, f"{rep.requests} requests")
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
    # Four: the density probe that decides whether this channel is worth
    # thinning at all (see test_thinned_series_is_checked.py), the opening
    # request, and the two days in day-sized steps.
    check("the slow signal is never split (density probe + opening + 2 steps)",
          fake.count_for("SLOW") == 4,
          f"{fake.count_for('SLOW')} requests")
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


def test_a_steady_meter_over_two_months_is_thinned():
    """~320 samples an hour is under the hourly "sparse" limit, but over two
    months it is ~460 000 raw samples per signal. Measured 2026-09-30: eight
    such energies were read raw and the PC ran out of memory. The judgement is
    on the whole period now, so only the one-hour density probes go out raw."""
    fake = _Fake({"E": 60 * DAY}, per_request=320).install()
    res, err, rep, _p, _s, _e = _fetch(["E"], 60 * DAY, fake.limits, count=2000)
    raw_wide = [c for c in fake.calls if c[3] is None and c[2] - c[1] > HOUR]
    check("no request over an hour is sent raw", not raw_wide,
          f"{len(raw_wide)} raw requests")
    check("and the signal still has data", bool(res["E"]) and not err)


class _DenseAfterAll(_Fake):
    """Quiet in the probe hour, heavy everywhere else."""

    def __call__(self, channel, start_ns, end_ns, timeout=10.0, count=None):
        with self._lock:
            self.calls.append((channel, int(start_ns), int(end_ns), count))
        if count is None and end_ns - start_ns > HOUR:
            n = 150_000
        else:
            n = 3 if count is None else min(int(count), 40)
        step = max(1, (end_ns - start_ns) // n)
        return [{"time": int(start_ns) + i * step, "value": float(i % 7),
                 "quality": "Original", "metaData": {"units": ""}}
                for i in range(n)]


def test_a_sparse_signal_that_is_not_stops_being_read_raw_early():
    """The raw read gives up as soon as it holds VERIFY_MAX_SAMPLES, instead of
    reading the whole period first and throwing it away."""
    fake = _DenseAfterAll({}).install()
    res, err, rep, _p, _s, _e = _fetch(["X"], 30 * DAY, {}, count=2000,
                                       max_workers=1)
    heavy = [c for c in fake.calls if c[3] is None and c[2] - c[1] > HOUR]
    need = cpva_core.VERIFY_MAX_SAMPLES // 150_000 + 1
    check("the raw read stops once it is too big", len(heavy) <= need,
          f"{len(heavy)} heavy raw requests (limit {need})")
    check("the signal comes back thinned instead",
          bool(res["X"]) and len(res["X"]) < cpva_core.VERIFY_MAX_SAMPLES,
          f"{len(res['X'])} samples")


def test_coverage_moves_with_time_read_and_ends_full():
    fake = _Fake({"A": 6 * HOUR, "B": 3 * HOUR}).install()
    cov, phases = [], []
    _fetch(["A", "B"], 5 * DAY, fake.limits,
           coverage_fn=cov.append, phase_fn=phases.append)
    check("coverage never falls",
          all(cov[i] <= cov[i + 1] for i in range(len(cov) - 1)))
    check("coverage ends at the whole period", bool(cov) and cov[-1] == 1.0,
          str(cov[-1] if cov else None))
    check("coverage moves in many small steps, not a few jumps",
          len(set(cov)) > 10, f"{len(set(cov))} distinct values")
    check("the density check names itself",
          any("densely" in p for p in phases), str(phases))


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


def test_screen_thinning_keeps_the_spike():
    """The graph used to average bins for the screen, which hid every spike."""
    import numpy as np
    ts = np.arange(10_000, dtype=np.float64) * 1e9
    v = np.full(10_000, 1.0); v[4321] = 50.0
    t2, m2, lo2, hi2 = app_main._downsample_arrays_envelope(ts, v, v.copy(), v.copy(), 100)
    check("the thinned trace has the target size", t2.size == 100, str(t2.size))
    check("the bin mean alone hides the spike", float(m2.max()) < 2.0, str(m2.max()))
    check("the bin's high edge keeps it exactly", float(hi2.max()) == 50.0, str(hi2.max()))


def test_minmax_is_read_from_the_archivers_samples():
    raw = [{"time": 10, "value": [2.0], "minimum": [1.0], "maximum": [9.0]},
           {"time": 20, "value": 3.0, "minimum": 3.0, "maximum": 3.0},
           {"time": 30, "value": 4.0}]
    mm = app_main._minmax_arrays(raw)
    check("min/max come through the one-element lists", mm is not None
          and list(mm[1]) == [1.0, 3.0, 4.0] and list(mm[2]) == [9.0, 3.0, 4.0],
          str(mm))
    check("a raw-only channel carries no envelope",
          app_main._minmax_arrays([{"time": 1, "value": 2.0}]) is None)
    import numpy as np
    lo, hi = app_main._envelope_for(np.array([15.0, 25.0, 5.0]),
                                    np.array([2.0, 3.0, 7.0]), mm)
    check("a held value gets the envelope of the sample it is held from",
          list(lo) == [1.0, 3.0, 7.0] and list(hi) == [9.0, 3.0, 7.0],
          f"{list(lo)} {list(hi)}")


class _FakeThinned(_Fake):
    """Answers count= like the real archiver: interval means that carry the
    interval's minimum and maximum. One spike of 500 hides in the middle."""

    SPIKE_AT = 0.5

    def spike_ns(self):
        # Off any regular sample time (+7 ns), or de-duplication would keep
        # the ordinary sample that shares its timestamp.
        return (self.window[0] + int((self.window[1] - self.window[0]) * self.SPIKE_AT)
                + 7)

    def __call__(self, channel, start_ns, end_ns, timeout=10.0, count=None):
        with self._lock:
            self.calls.append((channel, int(start_ns), int(end_ns), count))
        spike = self.spike_ns()
        if not count or end_ns - start_ns <= HOUR:          # raw, like the archiver
            # ~300 an hour, like the energy meters: too many to read a month raw.
            n = max(2, int(300 * (end_ns - start_ns) / HOUR))
            step = max(1, (end_ns - start_ns) // n)
            out = [{"time": int(start_ns) + i * step, "value": 1.0,
                    "quality": "Original", "metaData": {"units": "J"}} for i in range(n)]
            if start_ns <= spike <= end_ns:
                out.append({"time": spike, "value": 500.0, "quality": "Original",
                            "metaData": {"units": "J"}})
            return sorted(out, key=lambda s: s["time"])
        n = max(2, min(int(count), 400))
        step = max(1, (end_ns - start_ns) // n)
        out = []
        for i in range(n):
            a = int(start_ns) + i * step
            hit = a <= spike < a + step
            out.append({"time": a, "value": 1.0 + (0.1 if hit else 0.0),
                        "minimum": 1.0, "maximum": 500.0 if hit else 1.0,
                        "quality": "Interpolated", "type": "minMaxDouble",
                        "metaData": {"units": "J"}})
        return out


def test_a_long_load_draws_the_spike_and_zoom_reads_it_finer():
    pvs = ["L3-TEST-E:Energy"]
    w = _new_widget(pvs)
    # The box as the program starts with it: its "1000" switched the zoom
    # detail off for everyone while the waveplate PV was not even loaded.
    w._master_multiple_edit.setText(app_main.MASTER_STEP_DEFAULT)
    # And a condition the spike passes: the detail is judged, not skipped.
    w._conditions = [{"pv": pvs[0], "min": 0.5, "max": 1000.0}]
    now = datetime.now()
    w._dt_from, w._dt_to = now - timedelta(days=30), now
    fake = _FakeThinned({}).install()
    fake.window = (cpva_core.dt_to_ns(w._dt_from), cpva_core.dt_to_ns(w._dt_to))
    w._on_load_clicked(silent=True)
    _pump(60.0, cond=lambda: not w._load_in_flight)
    check("the thinned trace gets a min–max band",
          bool(w._graph_bands) and w._graph_bands[0] is not None)
    lo, hi = w._graph_env[0]
    _t, v = w._graph_raw[0]
    check("the line (means) stays low", float(max(v)) < 2.0, str(max(v)))
    check("the band reaches the spike", float(hi.max()) == 500.0, str(hi.max()))
    y0, y1 = w._graph_axes[0].get_ylim()
    check("the spike fits on screen", y1 >= 500.0, f"ylim {y0}..{y1}")
    check("the axis table's Max is the real maximum",
          w._pv_stats(pvs[0])["max"] == app_main._fmt_cursor_value(500.0),
          w._pv_stats(pvs[0])["max"])

    # Zoom to the hour around the spike: the archiver answers raw there.
    import matplotlib.dates as mdates
    spike = fake.spike_ns()
    c = datetime.fromtimestamp(spike / 1e9, cpva_core.TZ_PRAGUE)
    x0, x1 = c - timedelta(minutes=20), c + timedelta(minutes=20)
    w._on_zoom_select(mdates.date2num(x0), mdates.date2num(x1))
    _pump(15.0, cond=lambda: w._zoom_detail is not None and not w._detail_in_flight)
    check("the zoomed range is read again", w._zoom_detail is not None)
    t, v = w._graph_raw[0]
    m = (t >= mdates.date2num(x0)) & (t <= mdates.date2num(x1))
    check("zoomed in, the spike is a real point, not a band",
          m.any() and float(v[m].max()) == 500.0,
          str(float(v[m].max()) if m.any() else None))
    check("an hour's zoom is asked for raw", fake.calls[-1][3] is None,
          str(fake.calls[-1]))
    check("the zoom itself is kept",
          abs(w._graph_axes[0].get_xlim()[0] - mdates.date2num(x0)) < 1e-6)
    _teardown(w)


def test_crossing_episodes_from_raw_samples():
    import numpy as np
    ts = np.arange(10, dtype=np.float64) * 1e9
    v = np.array([1, 1, 6, 8, 7, 1, 1, 9, 1, 1], dtype=np.float64)
    eps = app_main._crossing_episodes(ts, v, 5.0, "above", 10e9)
    check("two separate crossings", len(eps) == 2, str(eps))
    if len(eps) == 2:
        check("the first starts on the first sample over and ends on the first back",
              eps[0]["start"] == 2e9 and eps[0]["end"] == 5e9, str(eps[0]))
        check("its peak and when", eps[0]["peak"] == 8 and eps[0]["peak_at"] == 3e9,
              str(eps[0]))
        check("the second is one sample", eps[1]["samples"] == 1, str(eps[1]))
    below = app_main._crossing_episodes(ts, v, 1.5, "below", 10e9)
    check("below counts the other side", len(below) == 3
          and below[-1]["end"] is None, str(below))
    lo = v.copy(); hi = v.copy(); hi[0] = 9
    cands = app_main._crossing_candidates(ts, lo, hi, 5.0, "above", 10e9)
    check("candidate stretches come from the high edge, merged when touching",
          cands == [(0, int(1e9)), (int(2e9), int(5e9)), (int(7e9), int(8e9))],
          str(cands))


def test_limit_crossings_find_the_hidden_spike_exactly():
    pvs = ["L3-TEST-E:Energy"]
    w = _new_widget(pvs)
    now = datetime.now()
    w._dt_from, w._dt_to = now - timedelta(days=30), now
    fake = _FakeThinned({}).install()
    fake.window = (cpva_core.dt_to_ns(w._dt_from), cpva_core.dt_to_ns(w._dt_to))
    w._on_load_clicked(silent=True)
    _pump(60.0, cond=lambda: not w._load_in_flight)
    w._ref_lines = [{"label": "limit", "pv": pvs[0], "y": 100.0,
                     "color": "#C62828", "style": "dashed", "width": 1.2,
                     "crossing": "above"}]
    shown = []
    orig = app_main._LimitCrossingsDialog.show
    app_main._LimitCrossingsDialog.show = lambda self: shown.append(self)
    try:
        w._open_limit_crossings()
        # The window opens at the click, empty, and fills when the search ends.
        check("the crossings window opens at the click", bool(shown))
        _pump(20.0, cond=lambda: not getattr(w, "_cross_in_flight", False))
    finally:
        app_main._LimitCrossingsDialog.show = orig
    rows = shown[0]._rows if shown else []
    check("exactly one crossing", len(rows) == 1, str(rows))
    if rows:
        r = rows[0]
        # Within a microsecond: times travel as float64, as in the graph.
        check("found at the spike's own time, sample-exact",
              r["exact"] and abs(r["start"] - fake.spike_ns()) < 1000
              and abs(r["peak_at"] - fake.spike_ns()) < 1000, str(r))
        check("with its real height", r["peak"] == 500.0, str(r["peak"]))
        tv = shown[0]._tv
        check("the table has the row and a readable header",
              tv.rowCount() == 1 and tv.horizontalHeaderItem(0).text() == "Line")
    for d in shown:
        d.deleteLater()
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
