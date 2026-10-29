"""Image Finder's PV window while its graph loads (27.10.2026).

The report: "I ticked PTM1 and SBW4 and the program froze — I could not change
anything while it loaded the graph. Two PVs are in the conditions, the graph shows
one. SBW4 is ticked in the conditions by default, PTM1 cannot be ticked until the
conditions are switched on."

Pinned here:

  * **A PV is read in pieces.** `cpva._fetch_values_pieced` covers the asked hours
    piece by piece, with no sample twice at a piece edge and the ".value" probe on
    the first piece only — so no single archiver answer is parsed for seconds while
    the window waits for Python's lock.
  * **The window keeps running while a load runs.** A slow archiver (stubbed) does
    not stop the window's own timer, and a PV ticked in the middle of a load ends
    up drawn next to the first one.
  * **A ticked PV is never missing from the graph.** Still loading, empty or
    unanswered, it is in the legend saying which.
  * **No condition row is ticked until the operator ticks it.**

The archiver is never touched.

    python testing/test_finder_load_responsive.py
"""
import sys
import threading
import time
import types
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder, prague_bounds
from test_pv_graph import DAY, ENERGY_CH, ENERGY2_CH, curves

FAILURES: "list[str]" = []

HOUR_NS = 3600 * 1_000_000_000


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# ── the archiver, as the tests want it ─────────────────────────────────────────
ARCHIVE: dict = {}          # channel → samples
STATUS: dict = {}           # channel → forced status ("error", …)
DELAY_S: dict = {}          # channel → seconds the fetch takes
GATE: dict = {}             # channel → threading.Event the fetch waits for


def fake_fetch(channel, start_ns, end_ns):
    gate = GATE.get(channel)
    if gate is not None:
        gate.wait(10.0)
    time.sleep(DELAY_S.get(channel, 0.0))
    if channel in STATUS:
        return [], STATUS[channel], ""
    out = [(t, v) for (t, v) in ARCHIVE.get(channel, []) if start_ns <= t <= end_ns]
    return out, ("ok" if out else "empty"), ""


def install_stubs(m):
    orig = (m.PVRegionSearchDialog._fetch_window,
            m.cpva.classify_step_channel, m.cpva.value_at_or_before)
    m.PVRegionSearchDialog._fetch_window = staticmethod(fake_fetch)
    m.cpva.classify_step_channel = lambda ch, ts_ns, primary=None, **kw: False
    m.cpva.value_at_or_before = (
        lambda ch, ts_ns, **kw: types.SimpleNamespace(ts_ns=None, value=None,
                                                      status="ok"))
    return orig


def restore_stubs(m, orig):
    (m.PVRegionSearchDialog._fetch_window,
     m.cpva.classify_step_channel, m.cpva.value_at_or_before) = orig


def reset_archive(samples: dict):
    ARCHIVE.clear(); ARCHIVE.update(samples)
    STATUS.clear(); DELAY_S.clear(); GATE.clear()


def new_dialog(m):
    from PySide6.QtCore import QDate
    cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    return m.PVRegionSearchDialog(cams, [QDate(DAY.year, DAY.month, DAY.day)])


def tick(dlg, channels, on=True):
    from PySide6.QtCore import Qt
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) in channels:
            it.setCheckState(Qt.CheckState.Checked if on
                             else Qt.CheckState.Unchecked)


def only(dlg, channels):
    from PySide6.QtCore import Qt
    dlg._pv_list.blockSignals(True)
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        on = it.data(Qt.ItemDataRole.UserRole) in channels
        it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
    dlg._pv_list.blockSignals(False)
    dlg._refresh_primary_combo()


def legend_labels(dlg):
    return list(curves(dlg))


def label_of(dlg, ch):
    return dlg._pv_label_for(ch).replace("\n", " ")


# ── 1. pieces ──────────────────────────────────────────────────────────────────
def check_pieces():
    print("=== a PV's hours are read in pieces ===")
    cpva = sys.modules["cpva_client"]
    calls: list = []
    start = 1_000 * HOUR_NS
    end = start + 14 * HOUR_NS - 1
    # One sample a minute, and every answer also carries the sample before its
    # piece — the archiver's carry-over value — which must not come out twice.
    data = [(start - 30 * 1_000_000_000, -1.0)] + [
        (start + i * 60_000_000_000, float(i)) for i in range(14 * 60)]

    def fake_ex(channel, lo, hi, *, timeout=0, try_value_suffix=True):
        calls.append((channel, lo, hi, try_value_suffix))
        before = [s for s in data if s[0] < lo][-1:]
        return before + [s for s in data if lo <= s[0] <= hi], channel

    orig = cpva.fetch_values_ex
    cpva.fetch_values_ex = fake_ex
    try:
        got, src = cpva._fetch_values_pieced("X:Energy", start, end, 2 * HOUR_NS)
    finally:
        cpva.fetch_values_ex = orig
    check("14 hours in 2-hour pieces is 7 requests", len(calls) == 7,
          f"{len(calls)}")
    check("the pieces touch end to end",
          all(calls[i + 1][1] == calls[i][2] + 1 for i in range(len(calls) - 1))
          and calls[0][1] == start and calls[-1][2] == end)
    ts = [t for t, _v in got]
    check("no sample twice", len(ts) == len(set(ts)), f"{len(ts)} vs {len(set(ts))}")
    check("every sample of the window is there (plus the one before it)",
          len(got) == 14 * 60 + 1, f"{len(got)}")
    check("in time order", ts == sorted(ts))
    check("the '.value' name is tried on the first piece only",
          [c[3] for c in calls] == [True] + [False] * 6, str([c[3] for c in calls]))
    check("the name that answered is returned", src == "X:Energy", src)


# ── 2. the window keeps running ────────────────────────────────────────────────
def check_responsive(m, energy, energy2):
    print("\n=== the window keeps running while the graph loads ===")
    from PySide6.QtCore import QTimer
    reset_archive({ENERGY_CH: energy, ENERGY2_CH: energy2})
    DELAY_S[ENERGY_CH] = 1.5
    DELAY_S[ENERGY2_CH] = 1.5
    dlg = new_dialog(m)
    only(dlg, (ENERGY_CH,))
    gaps: list = []
    last = [time.monotonic()]

    def beat():
        now = time.monotonic()
        gaps.append(now - last[0])
        last[0] = now
    hb = QTimer()
    hb.setInterval(50)
    hb.timeout.connect(beat)
    hb.start()
    dlg._reload_series()
    # In the middle of the first PV's read, the second one is ticked.
    B.wait_for(lambda: False, timeout_s=0.5)
    tick(dlg, (ENERGY2_CH,))
    B.wait_for(lambda: all(ch in (dlg._series.get(DAY) or {})
                           for ch in (ENERGY_CH, ENERGY2_CH))
               and not dlg._pending, timeout_s=15.0)
    hb.stop()
    worst = max(gaps) if gaps else 99.0
    check("the window's own timer kept ticking during the load",
          len(gaps) > 20 and worst < 1.0,
          f"{len(gaps)} beats, longest gap {worst * 1000:.0f} ms")
    labels = legend_labels(dlg)
    check("the PV ticked mid-load is drawn next to the first",
          label_of(dlg, ENERGY_CH) in labels and label_of(dlg, ENERGY2_CH) in labels,
          str(labels))
    dlg.close()


# ── 3. a ticked PV is never missing ────────────────────────────────────────────
def check_no_curve_notes(m, energy, energy2):
    print("\n=== a ticked PV with no curve says why ===")
    # SBW4 is held back while PTM1 answers — read side by side, PTM1 must reach the
    # graph without waiting for SBW4. Gated BEFORE the window opens: it loads its
    # default PVs on opening, and an answer that got through then is held and never
    # asked for again.
    reset_archive({ENERGY2_CH: energy2})
    GATE[ENERGY_CH] = threading.Event()
    dlg = new_dialog(m)
    only(dlg, (ENERGY_CH, ENERGY2_CH))
    dlg._reload_series()
    B.wait_for(lambda: ENERGY2_CH in (dlg._series.get(DAY) or {}), timeout_s=5.0)
    B.wait_for(lambda: False, timeout_s=0.6)     # the throttled redraw lands
    labels = legend_labels(dlg)
    want = f"{label_of(dlg, ENERGY_CH)} — loading…"
    check("still loading: in the legend as 'loading…'", want in labels, str(labels))
    check("next to the PV already drawn", label_of(dlg, ENERGY2_CH) in labels,
          str(labels))
    GATE[ENERGY_CH].set()
    B.wait_for(lambda: not dlg._pending, timeout_s=10.0)
    labels = legend_labels(dlg)
    want = f"{label_of(dlg, ENERGY_CH)} — no data in these hours"
    check("empty: in the legend as 'no data in these hours'", want in labels,
          str(labels))
    dlg.close()

    # The other way round: PTM1 (first on the list) held back, SBW4 still arrives.
    reset_archive({ENERGY_CH: energy})
    GATE[ENERGY2_CH] = threading.Event()
    dlg = new_dialog(m)
    only(dlg, (ENERGY_CH, ENERGY2_CH))
    dlg._reload_series()
    got = B.wait_for(lambda: ENERGY_CH in (dlg._series.get(DAY) or {}),
                     timeout_s=3.0)
    check("a slow PV first on the list does not hold the next one back", got)
    GATE[ENERGY2_CH].set()
    B.wait_for(lambda: not dlg._pending, timeout_s=10.0)
    dlg.close()

    reset_archive({ENERGY_CH: energy})
    STATUS[ENERGY2_CH] = "error"
    dlg = new_dialog(m)
    only(dlg, (ENERGY_CH, ENERGY2_CH))
    dlg._reload_series()
    B.wait_for(lambda: ENERGY2_CH in (dlg._series.get(DAY) or {})
               and not dlg._pending, timeout_s=10.0)
    labels = legend_labels(dlg)
    want = f"{label_of(dlg, ENERGY2_CH)} — archiver did not answer"
    check("unanswered: in the legend as 'archiver did not answer'", want in labels,
          str(labels))
    check("and the PV with data is drawn as usual",
          label_of(dlg, ENERGY_CH) in labels, str(labels))
    dlg.close()


# ── 4. conditions ──────────────────────────────────────────────────────────────
def check_conditions(m, energy, energy2):
    print("\n=== no condition row is ticked until the operator ticks it ===")
    reset_archive({ENERGY_CH: energy, ENERGY2_CH: energy2})
    dlg = new_dialog(m)
    only(dlg, (ENERGY_CH, ENERGY2_CH))
    rows = dlg._cond_rows
    check("a row per ticked PV", set(rows) >= {ENERGY_CH, ENERGY2_CH}, str(list(rows)))
    check("none of them ticked", not any(r["box"].isChecked() for r in rows.values()),
          str({ch: r["box"].isChecked() for ch, r in rows.items()}))
    check("the master box alone is not a condition search", not dlg._cond_is_on())
    dlg._cond_on.setChecked(True)
    check("master box on: the rows can be ticked", dlg._cond_box.isEnabled())
    rows[ENERGY2_CH]["box"].setChecked(True)
    rows[ENERGY_CH]["box"].setChecked(True)
    on = [r["channel"] for r in dlg._cond_rows_on()]
    check("both ticked rows are in the search", set(on) == {ENERGY_CH, ENERGY2_CH},
          str(on))
    dlg.close()


def main():
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    orig = install_stubs(m)
    try:
        base = prague_bounds(DAY, 10)
        energy = [(base + i * 60_000_000_000, 5.0 + i * 0.5) for i in range(12)]
        energy2 = [(base + i * 60_000_000_000, 1.0 + i * 0.1) for i in range(12)]
        check_pieces()
        check_responsive(m, energy, energy2)
        check_no_curve_notes(m, energy, energy2)
        check_conditions(m, energy, energy2)
    finally:
        restore_stubs(m, orig)
    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
