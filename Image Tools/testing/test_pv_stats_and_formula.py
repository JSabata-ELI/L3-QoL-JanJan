"""The PV window's two ported halves: the range statistics and a formula over time.

STATISTICS. A left drag marks a region; the table under the graph gives count, mean,
spread and the extremes for it, per PV — and a PV with NO sample inside the range
still gets a row, showing the value it was already sitting at, in amber, with
`n = 0` and the word "held". Three dashes there read as "this channel is broken",
which is the one thing the range must not say about a setting that did not move.

A FORMULA OVER TIME. `is_t.pv_eval_derived` answers "what is this formula worth at
one moment"; the graph needs the same answer at every moment of the window. The
engine ported out of One Moment builds a time base from the formula's own leaf
sources, holds each source forward onto it, and evaluates — vectorised where that is
provably the same answer, point by point where it is not.

Offscreen, no share and no archiver.
"""
import sys
import types
from datetime import date, datetime
from pathlib import Path

import numpy as np

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY = date(2026, 9, 1)
ENERGY_CH = "L3-SBW4-PM311:Energy"                    # registry SBW4, unit J
PTM1_CH = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"       # registry PTM1, unit J
STEP_CH = "L3-PFWP6-MTR03-1:RawPos"                   # a setting, written on change

ARCHIVE: dict = {}
STEPS: set = set()
SEED_VALUE = 42.5


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns_at(hour: int, minute: int = 0, sec: int = 0) -> int:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(DAY.year, DAY.month, DAY.day, hour, minute, sec,
                        tzinfo=tz).timestamp() * 1e9)


def install_stubs(m):
    def fake_fetch(channel, start_ns, end_ns):
        out = [(t, v) for (t, v) in ARCHIVE.get(channel, [])
               if start_ns <= t <= end_ns]
        return out, ("ok" if out else "empty"), ""

    orig = (m.PVRegionSearchDialog._fetch_window,
            m.cpva.classify_step_channel, m.cpva.value_at_or_before,
            m.cpva.is_step_channel)
    m.PVRegionSearchDialog._fetch_window = staticmethod(fake_fetch)
    m.cpva.classify_step_channel = (
        lambda ch, ts_ns, primary=None, **kw: ch in STEPS)
    m.cpva.is_step_channel = lambda ch, *a, **kw: ch in STEPS
    m.cpva.value_at_or_before = (
        lambda ch, ts_ns, **kw: types.SimpleNamespace(
            ts_ns=(ts_ns - 86_400_000_000_000) if ch in STEPS else None,
            value=(SEED_VALUE if ch in STEPS else None), status="ok"))
    return orig


def restore_stubs(m, orig):
    (m.PVRegionSearchDialog._fetch_window, m.cpva.classify_step_channel,
     m.cpva.value_at_or_before, m.cpva.is_step_channel) = orig


def make_dialog(m, checked=()):
    from PySide6.QtCore import QDate, Qt
    cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    dlg = m.PVRegionSearchDialog(cams, [QDate(DAY.year, DAY.month, DAY.day)])
    if checked:
        dlg._pv_list.blockSignals(True)
        for i in range(dlg._pv_list.count()):
            it = dlg._pv_list.item(i)
            on = it.data(Qt.ItemDataRole.UserRole) in checked
            it.setCheckState(Qt.CheckState.Checked if on
                             else Qt.CheckState.Unchecked)
        dlg._pv_list.blockSignals(False)
        dlg._refresh_primary_combo()
        dlg._reload_series()
    B.wait_for(lambda: bool(dlg._series), timeout_s=15.0)
    dlg._redraw()
    return dlg


def add_region(dlg, h0, h1):
    dlg._regions.append({"id": dlg._region_seq, "t_start_ns": ns_at(h0),
                         "t_end_ns": ns_at(h1), "color": "#C62828", "day": DAY,
                         "no": dlg._next_pick_no()})
    dlg._region_seq += 1
    dlg._regions_changed()


# The statistics have no table of their own any more: they are the right-hand
# columns of the ONE picks table. These are the columns they landed in.
C_DAY, C_TIME, C_PV, C_N, C_MEAN, C_STD, C_MIN, C_MAX = 3, 4, 6, 7, 8, 9, 10, 11


def cell(dlg, row, col):
    it = dlg._pick_table.item(row, col)
    return (it.text(), it.foreground().color().name(), it.toolTip()) if it else None


def check_stats(m):
    ARCHIVE.clear()
    STEPS.clear()
    STEPS.add(STEP_CH)
    # SBW4 every 10 s from 08:00 to 12:00, values 10.0 … 11.0; the setting never
    # moves today, so it has NO sample at all.
    ARCHIVE[ENERGY_CH] = [(ns_at(8) + i * 10_000_000_000, 10.0 + (i % 11) / 10.0)
                          for i in range(1440)]
    ARCHIVE[STEP_CH] = []
    dlg = make_dialog(m, checked=(ENERGY_CH, STEP_CH))
    B.wait_for(lambda: bool(dlg._seeds), timeout_s=15.0)

    check("nothing picked yet, so the table is empty",
          dlg._pick_table.rowCount() == 0)
    check("and it says what to do", "Click" in dlg._lbl_picks.text(),
          dlg._lbl_picks.text())

    add_region(dlg, 9, 10)
    check("the range is counted in the caption",
          "1 region" in dlg._lbl_picks.text(), dlg._lbl_picks.text())
    check("one row per checked PV", dlg._pick_table.rowCount() == 2,
          f"{dlg._pick_table.rowCount()} row(s)")
    day_c, time_c = dlg._pick_table.item(0, C_DAY), dlg._pick_table.item(0, C_TIME)
    check("the range names its DAY and both its times",
          day_c is not None and time_c is not None
          and DAY.strftime("%d.%m.%Y") in day_c.text()
          and "09:00:00" in time_c.text() and "10:00:00" in time_c.text(),
          f"{day_c.text() if day_c else '?'}  {time_c.text() if time_c else '?'}")
    check("and the pick's own cells are merged down the PVs of that range",
          dlg._pick_table.rowSpan(0, 0) == 2,
          str(dlg._pick_table.rowSpan(0, 0)))

    rows = {dlg._pick_table.item(r, C_PV).text(): r
            for r in range(dlg._pick_table.rowCount())}
    r_e = rows.get("SBW4")
    check("SBW4 has a row", r_e is not None, repr(list(rows)))
    n_txt, _ink, tip = cell(dlg, r_e, C_N)
    # 09:00:00 to 10:00:00 inclusive, one sample every 10 s = 361.
    check("it counted the samples in the hour", n_txt == "361",
          f"n = {n_txt}")
    mean_txt = cell(dlg, r_e, C_MEAN)[0]
    check("the mean is the archived value, not a normalised one",
          10.0 <= float(mean_txt) <= 11.0, mean_txt)
    check("min and max are the real extremes",
          cell(dlg, r_e, C_MIN)[0] == "10" and cell(dlg, r_e, C_MAX)[0] == "11",
          f"{cell(dlg, r_e, C_MIN)[0]} … {cell(dlg, r_e, C_MAX)[0]}")
    for want in ("median", "peak-to-peak", "trend"):
        check(f"the tooltip carries the {want}", want in tip, repr(tip[:60]))

    r_s = rows.get("WAVEPLATE")
    check("the setting has a row too, not left off", r_s is not None,
          repr(list(rows)))
    n_txt, ink, tip = cell(dlg, r_s, C_N)
    check("with n = 0", n_txt == "0", n_txt)
    check("and the spread reads 'held', never a dash",
          cell(dlg, r_s, C_STD)[0] == "held", cell(dlg, r_s, C_STD)[0])
    check("in amber, so it cannot be read as a mean of real samples",
          ink.lower() == "#8a6114", ink)
    check("the value is the one it was already sitting at",
          cell(dlg, r_s, C_MEAN)[0] == f"{SEED_VALUE:.4g}",
          cell(dlg, r_s, C_MEAN)[0])
    check("and the extremes are that same value, not 'unknown'",
          cell(dlg, r_s, C_MIN)[0] == cell(dlg, r_s, C_MAX)[0]
          == f"{SEED_VALUE:.4g}")
    check("the tooltip says it is held and why",
          "Held forward" in tip and "when it changes" in tip, repr(tip[-80:]))

    # A second region: BOTH are in the table, so two ranges can be read against
    # each other. There is no second page and no drop-down — that is the point.
    add_region(dlg, 11, 12)
    check("both ranges are in the table at once",
          dlg._pick_table.rowCount() == 4, f"{dlg._pick_table.rowCount()} row(s)")
    times = [dlg._pick_table.item(r, C_TIME).text()
             for r in (0, 2) if dlg._pick_table.item(r, C_TIME) is not None]
    check("each with its own times", len(times) == 2
          and "09:00:00" in times[0] and "11:00:00" in times[1], repr(times))
    check("and the caption counts them",
          "2 regions" in dlg._lbl_picks.text(), dlg._lbl_picks.text())
    check("no drop-down is left to pick one range",
          not hasattr(dlg, "_stats_cb"))
    check("and no page of its own either",
          not hasattr(dlg, "_stat_table") and not hasattr(dlg, "_bottom_tabs"))

    # A moment on the same table: it is one row, and it claims no statistics.
    dlg._set_moment_from_x(dlg._ns_to_x(ns_at(9) + 600_000_000_000, DAY))
    m_row = next(r for r in range(dlg._pick_table.rowCount())
                 if dlg._pick_table.item(r, 2) is not None
                 and dlg._pick_table.item(r, 2).text() == "Moment")
    check("a moment takes ONE row whatever is plotted",
          dlg._pick_table.rowSpan(m_row, 0) == 1,
          str(dlg._pick_table.rowSpan(m_row, 0)))
    check("and says dashes rather than a mean of one sample",
          cell(dlg, m_row, C_N)[0] == "—" and cell(dlg, m_row, C_MEAN)[0] == "—",
          f"{cell(dlg, m_row, C_N)[0]} / {cell(dlg, m_row, C_MEAN)[0]}")

    # A range with nothing anywhere near it: no samples, no seed.
    ARCHIVE[ENERGY_CH] = []
    dlg._series = {}
    dlg._seeds = {}
    dlg._reload_series()
    B.wait_for(lambda: bool(dlg._series), timeout_s=15.0)
    dlg._picks_changed(with_stats=True)
    rows = {}
    for r in range(dlg._pick_table.rowCount()):
        it = dlg._pick_table.item(r, C_PV)
        if it is not None and it.text():
            rows.setdefault(it.text(), r)
    n_txt, ink, tip = cell(dlg, rows["SBW4"], C_N)
    check("a PV with nothing at all says 0 and grey dashes",
          n_txt == "0" and cell(dlg, rows["SBW4"], C_MEAN)[0] == "—",
          f"{n_txt} / {cell(dlg, rows['SBW4'], C_MEAN)[0]}")
    check("and says nothing was archived up to that point",
          "none before it either" in tip, repr(tip[:70]))
    dlg.close()


def check_graph_controls(m):
    ARCHIVE.clear()
    ARCHIVE[ENERGY_CH] = [(ns_at(8) + i * 60_000_000_000, 10.0 + (i % 5))
                          for i in range(240)]
    dlg = make_dialog(m, checked=(ENERGY_CH,))

    check("the grid is on to begin with", dlg._ax.xaxis._major_tick_kw.get(
        "gridOn", True) or dlg._show_grid)
    dlg._cb_grid.setChecked(False)
    check("turning the grid off turns it OFF (grid(False, **style) turns it on)",
          not any(t.gridline.get_visible() for t in dlg._ax.get_xticklines()[:0])
          and dlg._show_grid is False)
    dlg._cb_grid.setChecked(True)

    check("there is a legend", dlg._ax.get_legend() is not None)
    dlg._cb_legend.setChecked(False)
    check("and it can be taken off", dlg._ax.get_legend() is None)
    dlg._cb_legend.setChecked(True)
    check("and put back", dlg._ax.get_legend() is not None)

    dlg._cb_logy.setChecked(True)
    check("log Y really is logarithmic", dlg._ax.get_yscale() == "log",
          dlg._ax.get_yscale())
    dlg._cb_logy.setChecked(False)

    # A hand-set value range survives the redraw that follows it.
    dlg._y_lim = (0.0, 100.0)
    dlg._redraw()
    check("a hand-set Y range survives the redraw",
          tuple(round(v) for v in dlg._ax.get_ylim()) == (0, 100),
          str(dlg._ax.get_ylim()))
    dlg._tick_min = 60
    dlg._redraw()
    ticks = dlg._ax.get_xticks()
    step = round((ticks[1] - ticks[0]) * 1440) if len(ticks) > 1 else 0
    check("the tick spacing is what was asked for", step == 60,
          f"{step} minutes")
    dlg._reset_view()
    check("Whole day clears the hand-set range",
          dlg._y_lim is None and dlg._tick_min == 0)

    # Own axis: two PVs of the same unit, one moved onto its own axis.
    ARCHIVE[PTM1_CH] = [(ns_at(8) + i * 60_000_000_000, 1200.0 + i)
                        for i in range(240)]
    from PySide6.QtCore import Qt
    dlg._pv_list.blockSignals(True)
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == PTM1_CH:
            it.setCheckState(Qt.CheckState.Checked)
    dlg._pv_list.blockSignals(False)
    dlg._refresh_primary_combo()
    dlg._reload_series()
    B.wait_for(lambda: PTM1_CH in (dlg._series.get(DAY) or {}), timeout_s=15.0)
    dlg._redraw()
    check("two PVs in joules share one axis", len(dlg._axes_extra) == 0,
          f"{1 + len(dlg._axes_extra)} axes")
    dlg._own_axis.add(PTM1_CH)
    dlg._redraw()
    check("and one of them can be given an axis of its own",
          len(dlg._axes_extra) == 1, f"{1 + len(dlg._axes_extra)} axes")
    dlg.close()


def check_formula(m):
    """A ratio over time, computed from two channels."""
    sl = m._get_slider_module()
    ARCHIVE.clear()
    STEPS.clear()
    # Two per-shot channels, published 40 ms apart for the same shot — the merge
    # window is what stops that becoming two points per shot.
    base = [ns_at(9) + i * 1_000_000_000 for i in range(120)]
    ARCHIVE[ENERGY_CH] = [(t, 10.0) for t in base]
    ARCHIVE[PTM1_CH] = [(t + 40_000_000, 2.0) for t in base]

    saved = list(sl.PV_DERIVED)
    sl.PV_DERIVED.append({"name": "RATIO", "expr": "A / B", "unit": "",
                          "bindings": {"A": "SBW4", "B": "PTM1"}})
    try:
        dlg = make_dialog(m, checked=(_derived_key(m, "RATIO"),))
        B.wait_for(
            lambda: _derived_key(m, "RATIO") in (dlg._series.get(DAY) or {}),
            timeout_s=20.0)
        got = (dlg._series.get(DAY) or {}).get(_derived_key(m, "RATIO")) or []
        check("the formula has a series over time", len(got) > 0,
              f"{len(got)} point(s)")
        check("one point per shot, not two",
              abs(len(got) - len(base)) <= 1, f"{len(got)} of {len(base)}")
        vals = [v for _t, v in got if v == v]
        check("and the value is the formula's answer",
              vals and all(abs(v - 5.0) < 1e-9 for v in vals),
              f"{vals[:3]}")
        dlg._redraw()
        labels = [ln.get_label() for ax in [dlg._ax] + list(dlg._axes_extra)
                  for ln in ax.get_lines()
                  if ln.get_label() and not ln.get_label().startswith("_")]
        check("it is drawn on the graph", any("RATIO" in l for l in labels),
              repr(labels))
        check("a formula is not offered as the PV to search by",
              dlg._primary_cb.findData(_derived_key(m, "RATIO")) < 0)
        dlg.close()

        # A formula whose letter stands for nothing says so instead of vanishing.
        sl.PV_DERIVED.append({"name": "BROKEN", "expr": "A / C", "unit": "",
                              "bindings": {"A": "SBW4"}})
        dlg = make_dialog(m, checked=(_derived_key(m, "BROKEN"),))
        B.wait_for(lambda: bool(dlg._derived_reason), timeout_s=20.0)
        why = dlg._derived_reason.get("BROKEN", "")
        check("an unbound letter is reported, not left blank",
              "stand for no PV" in why, repr(why))
        check("and it is on the status line", "BROKEN" in dlg._status.text(),
              dlg._status.text())
        dlg.close()
    finally:
        sl.PV_DERIVED[:] = saved


def _derived_key(m, name: str) -> str:
    return m._DERIVED_PREFIX + name


def check_engine(m):
    """The engine's own rules, without any Qt."""
    ts = np.asarray([0, 40_000_000, 1_000_000_000, 1_040_000_000], dtype=np.int64)
    merged = m._merge_base_ts([ts[:2], ts[2:]])
    check("near-simultaneous samples collapse onto the last of the group",
          merged.tolist() == [40_000_000, 1_040_000_000], merged.tolist())

    src = {"ts": np.asarray([0, 10_000_000_000], dtype=np.int64),
           "val": np.asarray([1.0, 2.0], dtype=np.float64), "channel": "X"}
    basis = np.asarray([5_000_000_000, 10_000_000_000], dtype=np.int64)
    out = m._align_source("X", src, basis)
    check("a source is held forward onto the time base",
          out.tolist() == [1.0, 2.0], out.tolist())
    early = m._align_source("X", src, np.asarray([-1], dtype=np.int64))
    check("and is NaN before its first sample",
          not np.isfinite(early[0]), str(early))
    seeded = m._align_source("X", src, np.asarray([-1], dtype=np.int64),
                             seed=(-2, 7.0))
    check("unless a seed says what it was already sitting at",
          seeded.tolist() == [7.0], seeded.tolist())

    ts2 = np.asarray([1, 2, 3, 4, 5], dtype=np.int64)
    val2 = np.asarray([1.0, np.nan, np.nan, 4.0, np.inf], dtype=np.float64)
    kt, kv = m._break_gaps(ts2, val2)
    check("a run of missing points leaves ONE NaN to break the line",
          kt.tolist() == [1, 2, 4, 5] and np.isnan(kv[1]),
          f"{kt.tolist()} {kv.tolist()}")
    check("and an infinity is written as NaN, never drawn",
          np.isnan(kv[-1]), kv.tolist())

    check("min() vetoes the vectorised path", not m._can_vectorise("min(A, B)"))
    check("a conditional does too", not m._can_vectorise("A if B else 0"))
    check("plain arithmetic does not", m._can_vectorise("A / B * 2"))


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    orig = install_stubs(m)
    try:
        print("=== the engine's rules ===")
        check_engine(m)
        print("\n=== statistics of the marked range ===")
        check_stats(m)
        print("\n=== the graph's own controls ===")
        check_graph_controls(m)
        print("\n=== a formula over time ===")
        check_formula(m)
    finally:
        restore_stubs(m, orig)
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
