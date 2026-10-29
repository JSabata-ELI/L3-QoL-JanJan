"""The selection counts real shots, and a custom PV has one point per shot.

* A shot is a moment when at least half of the channels running at the shot
  rate wrote. A channel that skips shots, one running free at 1 s and a slow
  housekeeping one (every 60 s) never decide it.
* The heading says the selection's length in d / h / min / s and the number of
  shots; a regular channel's card keeps its own number of samples (fewer when
  it skipped shots), a custom PV built on it has one value per shot.
* A window loaded thinned (one averaged point per time step) is counted from
  a raw read of the selection: the cards wait for it ("…"), then show the
  same exact numbers as an unthinned load.
* Live: a sample the archiver stored late is put into its place in time.

Headless, network mocked:

    python testing/test_shot_count_selection.py
"""
import gc
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox   # noqa: E402

import cpva_core                                 # noqa: E402
import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []
A_PV = "L3-TEST-A:Energy"      # on the shot rate, skips 13 of the 200 shots
B_PV = "L3-TEST-B:Energy"      # on the shot rate, every shot
E_PV = "L3-TEST-E:Energy"      # on the shot rate, every shot, 400 ms late
C_PV = "L3-TEST-C:Fast"        # runs free at 1 s
D_PV = "L3-TEST-D:Temp"        # housekeeping, every 60 s
PVS = [A_PV, B_PV, E_PV, C_PV, D_PV]
NS = 1_000_000_000

NOW = datetime.now().replace(microsecond=0)


def _setup(span_min):
    """Shots every 5 s, the last 200 of them inside the window's middle."""
    t_from = NOW - timedelta(minutes=span_min)
    t0 = int(t_from.timestamp()) * NS
    first = t0 + (span_min * 60 - 1100) * NS // 2
    shots = [first + k * 5 * NS for k in range(200)]
    skipped = set(range(7, 200, 15))           # 13 shots A did not write
    return t_from, t0, shots, skipped


STATE = {}


def _fake(channel, start_ns, end_ns, *a, **k):
    s, e = int(start_ns), int(end_ns)
    t0, t1 = STATE["t0"], STATE["t1"]
    out = []

    def add(t, v):
        if s <= t <= e:
            out.append({"time": int(t), "value": float(v), "metaData": {"units": ""}})

    if channel == C_PV:
        for t in range(t0, t1, NS):
            add(t + 333_000_000, 1.0)
    elif channel == D_PV:
        for t in range(t0, t1, 60 * NS):
            add(t + 17 * NS, 21.5)
    else:
        for i, t in enumerate(STATE["shots"]):
            if channel == A_PV and i in STATE["skipped"]:
                continue
            delay = 400_000_000 if channel == E_PV else (2_000_000 if channel == B_PV else 0)
            add(t + delay, 10.0 + i)
    return out


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


def _num(t_ns):
    return float(css._ns_to_num(np.float64(t_ns)))


def _card(w, pv):
    for name, _c, st in w._stats_rows:
        if name == w._col_label(pv):
            return st["n"]
    return None


def _card_n(w, pv):
    v = _card(w, pv)
    return int(v.replace(",", "")) if v and v[0].isdigit() else v


def unit_checks():
    t_from, t0, shots, skipped = _setup(30)
    STATE.update(t0=t0, t1=t0 + 1800 * NS, shots=shots, skipped=skipped)
    ts = {pv: np.array([d["time"] for d in _fake(pv, t0, t0 + 1800 * NS)],
                       dtype=np.float64) for pv in PVS}
    starts, ends, gap, members = cpva_core.find_shots(ts)
    check("the shot rate is the 5 s most channels share",
          gap is not None and abs(gap - 5 * NS) < 1e6, f"gap={gap}")
    check("the free runner and the housekeeping channel do not decide",
          sorted(members) == sorted([A_PV, B_PV, E_PV]), str(members))
    check("every one of the 200 shots is found, also where A skipped",
          starts.size == 200, f"{starts.size} shots")
    check("a late channel's write is the end of its own shot",
          bool(np.all(np.abs(ends - np.array(shots) - 400_000_000) < 1_000)))
    lone = cpva_core.find_shots({A_PV: ts[A_PV], C_PV: ts[C_PV], D_PV: ts[D_PV]})
    check("with one channel per rate, the slower one wins the tie",
          lone[2] is not None and lone[2] > 4 * NS, f"gap={lone[2]}")
    check("too few samples: no shot rate",
          cpva_core.find_shots({A_PV: ts[A_PV][:2]})[2] is None)
    f = css._fmt_span_full
    check("length to the second, every unit written out",
          (f(12.44), f(75), f(3600), f(93784), f(86400 + 300))
          == ("12.4 s", "1 min 15 s", "1 h", "1 d 2 h 3 min 4 s", "1 d 5 min"),
          str((f(12.44), f(75), f(3600), f(93784), f(86400 + 300))))


def _make_widget(span_min, avg):
    t_from, t0, shots, skipped = _setup(span_min)
    STATE.update(t0=t0, t1=t0 + span_min * 60 * NS, shots=shots, skipped=skipped)
    w = css.CSSLoggerWidget()
    w.resize(1400, 900)
    w._pv_list.clear()
    for pv in PVS:
        w._pv_list.addItem(pv)
    w._update_pv_count()
    w._master_pv_edit.setText("")
    w._master_multiple_edit.setText("")
    w._set_conditions([])
    w._custom_pvs = [{"name": "A2", "expr": "A*2", "bindings": {"A": A_PV}}]
    w._dt_from, w._dt_to = t_from, t_from + timedelta(minutes=span_min)
    w._chk_dead_shot.setChecked(False)
    w._chk_sel_pause.setChecked(False)
    w._avg_target_spin.setValue(avg)
    w._on_load_clicked()
    ok = _pump(30.0, lambda: (not w._load_in_flight) and w._graph_axes)
    check(f"a graph is drawn ({span_min} min window, Detail {avg})", ok)
    return w, shots, ok


def _close(w):
    w.close()
    w.deleteLater()
    _app.processEvents()


def selection_checks(span_min, avg):
    w, shots, ok = _make_widget(span_min, avg)
    if not ok:
        return
    thinned = bool(getattr(w, "_load_thinned", False))
    check("the load is thinned exactly when it should be",
          thinned == (avg > 0 and span_min > 60), f"thinned={thinned}")
    a_ns, b_ns = shots[0] - 1 * NS, shots[-1] + 1 * NS
    w._on_span_select(_num(a_ns), _num(b_ns))
    if thinned:
        check("thinned: the cards wait for the exact read",
              _card(w, B_PV) == "…", f"B N={_card(w, B_PV)}")
        _pump(20.0, lambda: _card(w, B_PV) not in (None, "…"))
    title = w._stats_title.text()
    check("the heading counts the 200 shots", "200 shots" in title, title)
    check("the heading gives the length in min and s",
          re.search(r"·\s+16 min 3\d s\s+·", title) is not None, title)
    check("A's card: its own 187 samples", _card_n(w, A_PV) == 187,
          f"N={_card_n(w, A_PV)}")
    check("B's card: 200", _card_n(w, B_PV) == 200, f"N={_card_n(w, B_PV)}")
    check("the custom PV on A: one value per shot, 200",
          _card_n(w, "A2") == 200, f"N={_card_n(w, 'A2')}")
    tip = w._stats_title.toolTip()
    check("the tooltip names the deciding channels",
          all(css.shorten_pv_name(p) in tip for p in (A_PV, B_PV, E_PV))
          and css.shorten_pv_name(C_PV) not in tip, tip)
    if not thinned:
        n_cpv = len(w._samples_by_pv.get("A2") or [])
        check("the custom trace over the whole window has one point per shot",
              n_cpv == 200, f"{n_cpv} points")
    _close(w)


def live_late_check():
    w, shots, ok = _make_widget(30, 0)
    if not ok:
        return
    s = w._samples_by_pv[A_PV]
    missing_t = shots[7]                       # one of A's skipped shots
    n0 = len(s)
    w._merge_late_live_samples({A_PV: [(missing_t, 99.0, "")]})
    check("a late sample goes into its place in time",
          len(s) == n0 + 1 and all(s[i][0] < s[i + 1][0] for i in range(len(s) - 1)))
    check("and the table is rebuilt for it", bool(getattr(w, "_live_rows_stale", False)))
    _close(w)


def main() -> int:
    print("test_shot_count_selection")
    for mod in (cpva_core, css):
        for fn in ("cpva_fetch_samples", "cpva_fetch_samples_chunked"):
            if hasattr(mod, fn):
                setattr(mod, fn, _fake)
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    for fn in ("save_config", "save_presets", "save_condition_presets",
               "save_custom_pvs"):
        setattr(css, fn, lambda *a, **k: None)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected

    unit_checks()
    selection_checks(30, 0)        # every sample loaded
    selection_checks(180, 50)      # thinned: counted from a raw read
    live_late_check()

    deadline = time.time() + 5
    while threading.active_count() > 1 and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.02)
    gc.collect()

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
