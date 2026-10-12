"""
"The last value must always be visible" — with the Conditions on.

A period with the laser off drew an EMPTY graph: every shot in it fails the
energy Conditions, and so does the laser-off value from just before it, so
nothing was left to hold. The rule is that the last good value is always shown,
however old — a day, two, a week — as a flat line, and that the plot SAYS it is
showing an old value.

What this pins:

  * _last_passing_moment finds the newest moment before the period at which
    every condition passes, walking back over many hours of laser-off,
  * it never answers with a sample stamped after the period (the archiver
    brackets a range on both sides),
  * it answers None when nothing passes within the look-back depth, and when
    the archiver cannot be read at all,
  * at widget level: a laser-off hour with the Conditions on draws every
    signal as a flat line at its value of the last good shot, and the plot
    carries a note naming that shot's time,
  * switching the Conditions off drops the held value again.

Runs headless with the network faked — no archiver needed.

Run:   python testing/test_last_good_shot.py    (exits non-zero on failure)
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
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
_APP = _HERE.parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="csslogger_test_")

import numpy as np
import cpva_core
import main as app_main
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

_app = QApplication.instance() or QApplication([])
_BASE_THREADS = threading.active_count()

HOUR = int(3600e9)
STEP = int(5e9)                       # one shot every 5 s, like the meters
FAILURES = []

E_PV = "L3-TEST-E:Energy"             # the condition channel (5 … 150 J)
P_PV = "L3-TEST-P:Position"           # a plain signal riding along


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _TooMuch(Exception):
    def __init__(self):
        super().__init__("HTTP 500")
        self.response = _Resp(500)


class _Laser:
    """A fake archiver: the laser fires 50 J shots until ``off_ns``, then
    reads -1.7 J (off) every 5 s up to now. The position channel holds 123
    while firing and 7 after. Replies are bracketed on both sides like the
    real archiver's."""

    def __init__(self, off_ns, dead=False):
        self.off_ns = off_ns
        self.dead = dead
        self.calls = 0
        self._lock = threading.Lock()

    def value(self, channel, t):
        on = t < self.off_ns
        if channel == E_PV:
            return 50.0 if on else -1.7
        return 123.0 if on else 7.0

    def __call__(self, channel, start_ns, end_ns, timeout=10.0, count=None):
        with self._lock:
            self.calls += 1
        if self.dead:
            raise ConnectionError("archiver unreachable")
        start_ns, end_ns = int(start_ns), int(end_ns)
        if count is None and end_ns - start_ns > 24 * HOUR:
            raise _TooMuch()
        first = (start_ns // STEP + 1) * STEP
        times = list(range(first, end_ns + 1, STEP))
        if count and len(times) > count:
            times = times[::max(1, len(times) // count)]
        times = [first - STEP] + times + [times[-1] + STEP if times else end_ns + STEP]
        return [{"time": t, "value": [self.value(channel, t)],
                 "quality": "Original", "metaData": {"units": "J"}}
                for t in times]


def _fetch_many(fake):
    """cpva_fetch_many_adaptive's shape over the fake, for the pure test."""
    def _f(pvs, lo, hi, **kw):
        out, errs = {}, {}
        for pv in pvs:
            try:
                out[pv] = fake(pv, lo, hi)
            except Exception as exc:
                errs[pv] = str(exc)
        return out, errs, None
    return _f


def test_hunt_walks_back_over_the_laser_off_hours():
    now = int(time.time() * 1e9) // STEP * STEP
    fake = _Laser(off_ns=now - 30 * HOUR)
    conds = [(E_PV, 5.0, 150.0, False)]
    t = app_main._last_passing_moment(conds, now - HOUR, 137_000_000,
                                      fetch_fn=_fetch_many(fake))
    check("the last good shot is found 30 h back",
          t is not None and fake.off_ns - STEP <= t < fake.off_ns,
          f"{(now - t) / HOUR:.2f} h before now" if t else "None")

    # The period starts just after the laser went off: the reply's trailing
    # bracket sample (a laser-off one) must not be what answers.
    t2 = app_main._last_passing_moment(conds, fake.off_ns + STEP, 137_000_000,
                                       fetch_fn=_fetch_many(fake))
    check("a sample after the period never answers",
          t2 is not None and t2 < fake.off_ns, str(t2))


def test_hunt_gives_up_cleanly():
    now = int(time.time() * 1e9) // STEP * STEP
    never = _Laser(off_ns=0)
    t = app_main._last_passing_moment([(E_PV, 5.0, 150.0, False)], now,
                                      137_000_000, fetch_fn=_fetch_many(never))
    check("nothing passing within the depth gives None", t is None, str(t))
    steps = app_main._LAST_PASS_DEPTH_NS // app_main._LAST_PASS_STEP_NS
    check("and stops at the depth", never.calls == steps,
          f"{never.calls} requests, depth {steps}")

    dead = _Laser(off_ns=now, dead=True)
    t = app_main._last_passing_moment([(E_PV, 5.0, 150.0, False)], now,
                                      137_000_000, fetch_fn=_fetch_many(dead))
    check("an unreadable archiver gives None at once",
          t is None and dead.calls == 1, f"{dead.calls} requests")


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
    w._custom_pvs = []
    w._pv_list.clear()
    for pv in pvs:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    if w._master_multiple_edit is not None:
        w._master_multiple_edit.setText("")
    return w


def _teardown(w, timeout_s=15.0):
    w._stop_live()
    w._load_epoch += 1
    w._lg_epoch += 1
    deadline = time.time() + timeout_s
    while threading.active_count() > _BASE_THREADS and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.02)
    w.close()
    w.deleteLater()
    _app.processEvents()
    gc.collect()
    _app.processEvents()


def test_laser_off_hour_draws_the_last_good_shot():
    now = datetime.now()
    off_ns = int((now - timedelta(hours=11)).timestamp() * 1e9) // STEP * STEP
    fake = _Laser(off_ns=off_ns)
    cpva_core.cpva_fetch_samples = fake
    w = _new_widget([E_PV, P_PV])
    rows = [app_main._cond_normalize({"pv": E_PV, "enabled": True,
                                      "kind": "between", "a": 5.0, "b": 150.0})]
    w._conditions_all = rows
    w._conditions_on = True
    w._conditions = [c for c in rows if c["enabled"]]
    w._dt_from, w._dt_to = now - timedelta(hours=1), now
    w._on_load_clicked(silent=True)
    check("the load finishes", _pump(60.0, cond=lambda: not w._load_in_flight))
    check("the Conditions reject every row of the hour", not w._table_rows,
          f"{len(w._table_rows)} rows")
    found = _pump(60.0, cond=lambda: (w._last_good or {}).get("hunting") is False)
    check("the look-back finishes", found, str(w._last_good))
    lg = w._last_good or {}
    check("it lands on the last 50 J shot",
          lg.get("ts") is not None and off_ns - STEP <= lg["ts"] < off_ns,
          str(lg.get("ts")))
    check("every signal gets that shot's value",
          lg.get("vals", {}).get(E_PV) == 50.0 and lg.get("vals", {}).get(P_PV) == 123.0,
          str(lg.get("vals")))

    drawn = {}
    for pv, lines in zip(w._graph_pvs, w._graph_lines):
        y = np.asarray(lines[-1].get_ydata(), dtype=float)
        drawn[pv] = sorted(set(y[np.isfinite(y)].tolist()))
    check("both signals are drawn as one flat line at the good value",
          drawn.get(E_PV) == [50.0] and drawn.get(P_PV) == [123.0], str(drawn))

    texts = [t.get_text() for ax in (w._graph_axes or []) for t in ax.texts]
    note = next((t for t in texts if "last shot that did" in t), "")
    check("the plot says it is holding an old shot", bool(note), str(texts))
    when = datetime.fromtimestamp(lg.get("ts", 0) / 1e9, cpva_core.TZ_PRAGUE)
    check("…and names when it was", f"{when:%H:%M:%S}" in note, note)

    w._set_conditions_on(False)
    _pump(0.2)
    check("with the Conditions off nothing old is held",
          w._last_good is None, str(w._last_good))
    _teardown(w)


if __name__ == "__main__":
    real_fetch = cpva_core.cpva_fetch_samples
    try:
        test_hunt_walks_back_over_the_laser_off_hours()
        test_hunt_gives_up_cleanly()
        test_laser_off_hour_draws_the_last_good_shot()
    finally:
        cpva_core.cpva_fetch_samples = real_fetch
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        sys.exit(1)
    print("all checks passed")
