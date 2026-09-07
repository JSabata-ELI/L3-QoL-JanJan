"""The PV window's graph: real values, one axis per unit, and a held value held.

The Image Finder's PV window used to divide every curve by that PV's own biggest
value. Every curve then filled the height of the plot and a reading of 0.9 meant
nothing but "near its own maximum" — two PVs could not be compared with each other,
and two days of the same PV could not be compared either.

What it does now, and what is pinned here:

  * **Nothing is normalised.** What is drawn is what was archived.
  * **One y axis per unit.** Joules on one axis, millimetres on another, a PV with no
    unit on its own — grouped by the unit the shared PV registry gives, so this window
    and the Slider say the same thing about the same PV.
  * **A channel that holds its value is held.** A setting written only when it CHANGES
    has no samples at all on a day it did not move; its curve starts at the left edge
    from the value it was already sitting at and is carried to the right edge, drawn
    steps-post. Those edge points are synthetic, so they get no marker — a dot means a
    sample was recorded.
  * **A curve's colour comes from the PV**, not from where it sits among the ones being
    drawn: unchecking one PV must not recolour the others.

The archiver is never touched — the fetch, the step-channel verdict and the
value-before-the-day are all replaced, so the test says nothing about whether this
machine was running yesterday.

    python testing/test_pv_graph.py
"""
import sys
import types
from datetime import date, datetime
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder, prague_bounds

FAILURES: "list[str]" = []

DAY = date(2026, 8, 17)
ENERGY_CH = "L3-SBW4-PM311:Energy"          # registry: SBW4, unit J
ENERGY2_CH = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"   # registry: PTM1, unit J
STEP_CH = "L3-PFWP6-MTR03-1:RawPos"         # registry: Waveplate, no unit, a setting
SEED_VALUE = 42.5


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


# What the stubbed archiver answers with. Set per case; the stubs themselves stay
# installed for the whole run, because the window reloads by itself whenever the PV
# list changes and a stub taken away too early means a real request to the archiver.
ARCHIVE: dict = {}
STEPS: set = set()


def install_stubs(m):
    def fake_fetch(channel, start_ns, end_ns):
        out = [(t, v) for (t, v) in ARCHIVE.get(channel, [])
               if start_ns <= t <= end_ns]
        # (samples, status, alias): the third value is the OTHER archived name
        # when that is what answered (SBW4's two names take turns). Nothing here
        # is aliased, so it is always empty.
        return out, ("ok" if out else "empty"), ""

    orig = (m.PVRegionSearchDialog._fetch_window,
            m.cpva.classify_step_channel, m.cpva.value_at_or_before)
    m.PVRegionSearchDialog._fetch_window = staticmethod(fake_fetch)
    m.cpva.classify_step_channel = (
        lambda ch, ts_ns, primary=None, **kw: ch in STEPS)
    m.cpva.value_at_or_before = (
        lambda ch, ts_ns, **kw: types.SimpleNamespace(
            ts_ns=(ts_ns - 86_400_000_000_000) if ch in STEPS else None,
            value=(SEED_VALUE if ch in STEPS else None), status="ok"))
    return orig


def restore_stubs(m, orig):
    (m.PVRegionSearchDialog._fetch_window,
     m.cpva.classify_step_channel, m.cpva.value_at_or_before) = orig


def make_dialog(m, samples_by_ch: dict, step_channels=(), checked=()):
    """A PV window whose archiver is a dictionary."""
    from PySide6.QtCore import QDate, Qt
    ARCHIVE.clear()
    ARCHIVE.update(samples_by_ch)
    STEPS.clear()
    STEPS.update(step_channels)
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
    B.wait_for(lambda: bool(dlg._series), timeout_s=10.0)
    dlg._redraw()
    return dlg


def curves(dlg):
    """Every drawn curve, by the label it carries, across all the y axes."""
    out = {}
    for axis in [dlg._ax] + list(dlg._axes_extra):
        for ln in axis.get_lines():
            lbl = ln.get_label()
            if lbl and not lbl.startswith("_"):
                out[lbl] = (ln, axis)
    return out


def main():
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    orig = install_stubs(m)
    try:
        return run_checks(m)
    finally:
        restore_stubs(m, orig)


def run_checks(m):
    base = prague_bounds(DAY, 10)
    energy = [(base + i * 60_000_000_000, 5.0 + i * 0.5) for i in range(12)]
    energy2 = [(base + i * 60_000_000_000, 120.0 + i) for i in range(12)]

    # ── real values ─────────────────────────────────────────────────────────
    print("=== the values drawn are the values archived ===")
    dlg = make_dialog(m, {ENERGY_CH: energy}, checked=(ENERGY_CH,))
    cs = curves(dlg)
    check("the PV is on the graph", len(cs) == 1, str(list(cs)))
    if cs:
        ln = list(cs.values())[0][0]
        ys = [y for y in ln.get_ydata() if y == y]
        check("the biggest value is the archived one, not 1.0",
              abs(max(ys) - 10.5) < 1e-6, f"{max(ys):.3f}")
        check("and the smallest is too", abs(min(ys) - 5.0) < 1e-6, f"{min(ys):.3f}")
        check("drawn as a value held until the next sample",
              ln.get_drawstyle().endswith("post"), ln.get_drawstyle())
    check("the y axis is labelled with the unit", dlg._ax.get_ylabel() == "J",
          dlg._ax.get_ylabel())
    dlg.close()

    # ── one axis per unit ───────────────────────────────────────────────────
    print("\n=== one y axis per unit ===")
    dlg = make_dialog(m, {ENERGY_CH: energy, ENERGY2_CH: energy2},
                      checked=(ENERGY_CH, ENERGY2_CH))
    check("two PVs in joules share one axis", len(dlg._axes_extra) == 0,
          f"{1 + len(dlg._axes_extra)} axes")
    check("both are drawn", len(curves(dlg)) == 2, str(list(curves(dlg))))
    ys_all = [y for ln, _ax in curves(dlg).values() for y in ln.get_ydata() if y == y]
    check("on one scale, so they can be compared", max(ys_all) > 100.0,
          f"biggest {max(ys_all):.1f}")
    dlg.close()

    dlg = make_dialog(m, {ENERGY_CH: energy, STEP_CH: []},
                      step_channels=(STEP_CH,), checked=(ENERGY_CH, STEP_CH))
    # A setting with no sample in the day is drawn from its SEED, and the seed
    # arrives in the loader's second pass — deliberately, so the archiver cannot
    # keep the first draw waiting. Wait for it rather than racing it.
    B.wait_for(lambda: bool(dlg._seeds), timeout_s=10.0)
    dlg._redraw()
    check("a PV with another unit gets an axis of its own",
          len(dlg._axes_extra) == 1, f"{1 + len(dlg._axes_extra)} axes")
    units = {dlg._ax.get_ylabel()} | {a.get_ylabel() for a in dlg._axes_extra}
    check("the axes are named apart", len(units) == 2, str(sorted(units)))

    # ── a held value is held ────────────────────────────────────────────────
    print("\n=== a setting with no sample today is still shown ===")
    cs = curves(dlg)
    step_label = [lbl for lbl in cs if "waveplate" in lbl.lower()]
    check("the setting is on the graph", bool(step_label), str(list(cs)))
    if step_label:
        ln, axis = cs[step_label[0]]
        ys = [y for y in ln.get_ydata() if y == y]
        xs = [x for x in ln.get_xdata() if x == x]
        check("held at the value it was already sitting at",
              bool(ys) and all(abs(y - SEED_VALUE) < 1e-6 for y in ys),
              f"{sorted(set(round(y, 2) for y in ys))}")
        check("across the whole day", len(xs) >= 2 and (max(xs) - min(xs)) > 0.3,
              f"{(max(xs) - min(xs)) if len(xs) >= 2 else 0:.2f} of a day")
        markers = [l2 for l2 in axis.get_lines()
                   if l2 is not ln and l2.get_linestyle() == "None"]
        check("with no dot on it — nothing was recorded there",
              all(len([y for y in l2.get_ydata() if y == y]) == 0 for l2 in markers)
              or not markers, f"{len(markers)} marker line(s)")
    dlg.close()

    # ── the colour belongs to the PV ────────────────────────────────────────
    print("\n=== a PV keeps its colour when another is unchecked ===")
    dlg = make_dialog(m, {ENERGY_CH: energy, ENERGY2_CH: energy2},
                      checked=(ENERGY_CH, ENERGY2_CH))
    before = {lbl: ln.get_color() for lbl, (ln, _a) in curves(dlg).items()}
    from PySide6.QtCore import Qt
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        if it.data(Qt.ItemDataRole.UserRole) == ENERGY_CH:
            it.setCheckState(Qt.CheckState.Unchecked)
    dlg._reload_series()
    B.wait_for(lambda: bool((dlg._series.get(DAY) or {}).get(ENERGY2_CH)),
               timeout_s=10.0)
    dlg._redraw()
    after = {lbl: ln.get_color() for lbl, (ln, _a) in curves(dlg).items()}
    check("the PV left checked is still drawn", len(after) == 1, str(list(after)))
    same = [lbl for lbl in after if lbl in before and after[lbl] == before[lbl]]
    check("and keeps the colour it had", after and len(same) == len(after),
          f"{after} was {before}")
    check("two PVs never share a colour", len(set(before.values())) == len(before),
          str(before))

    # Every PV on the list at once: the palette is shorter than the list can be, and
    # two curves that look identical are worse than an odd colour.
    styles = [dlg._style_for(dlg._pv_list.item(i).data(Qt.ItemDataRole.UserRole))
              for i in range(dlg._pv_list.count())]
    check("no two PVs on the list are drawn the same",
          len(set(styles)) == len(styles), str(styles))
    dlg.close()

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
