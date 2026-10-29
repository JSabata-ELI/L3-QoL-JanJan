"""PV Search window, 27.10.2026: the picked window, one PV at a time, a fast redraw.

1. **THE GRAPH READS ONLY THE PICKED TIME.** A day picked 07:00–21:00 in the Time
   window is fetched and drawn 07:00–21:00, not 00:00–23:59; a day with no window
   handed in gets the tab's default (07–21). "All days" maps x ↔ time inside each
   day's window and labels its ticks with the right clock times.
2. **EACH PV IS DRAWN AS IT ARRIVES.** With a slow archiver, the first PV is on the
   graph while the later ones are still being read.
3. **A BIG DAY DOES NOT FREEZE THE WINDOW.** 360 000 samples redraw well under a
   second, and a single spike among them is still on the drawn curve.
4. **ONE LINE PER CONDITION ROW.** Name, test and value sit side by side, and the
   value box is as wide as a number, not as wide as the sidebar.

Offscreen, no share and no archiver.

    python testing/test_pv_search_window_and_progressive.py
"""
import sys
import threading
import time
import types
from datetime import date, datetime
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_finder_moment import load_finder

FAILURES: "list[str]" = []
DAY = date(2026, 9, 10)
DAY2 = date(2026, 9, 16)
SBW4_CH = "L3-SBW4-PM311:Energy"
PTM1_CH = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"
PCM2_CH = "L3-PM03-025:Energy"
ARCHIVE: dict = {}
FETCHES: list = []            # (channel, start_ns, end_ns)
DELAY = {"s": 0.0}


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def ns_at(day: date, hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    return int(datetime(day.year, day.month, day.day, hour, minute,
                        tzinfo=ZoneInfo("Europe/Prague")).timestamp() * 1e9)


def fake_fetch(channel, start_ns, end_ns):
    if DELAY["s"]:
        # Since 27.10.2026 a day's PVs are read side by side, so each one takes
        # its own time here (SBW4 1×, PTM1 2×, PCM2 3×) — otherwise they would
        # all land at once and "drawn as it arrives" could not be told apart.
        k = {SBW4_CH: 1, PTM1_CH: 2, PCM2_CH: 3}.get(channel, 1)
        time.sleep(DELAY["s"] * k)
    FETCHES.append((channel, start_ns, end_ns))
    out = [(t, v) for (t, v) in ARCHIVE.get(channel, []) if start_ns <= t <= end_ns]
    return out, ("ok" if out else "empty"), ""


def install_stubs(m):
    m.PVRegionSearchDialog._fetch_window = staticmethod(fake_fetch)
    m._fetch_window_samples = fake_fetch
    m.cpva.classify_step_channel = lambda ch, ts_ns, primary=None, **kw: False
    m.cpva.is_step_channel = lambda ch, *a, **kw: False
    m.cpva.value_at_or_before = (
        lambda ch, ts_ns, **kw: types.SimpleNamespace(ts_ns=None, value=None,
                                                      status="ok"))


def set_ticks(dlg, checked):
    from PySide6.QtCore import Qt
    dlg._pv_list.blockSignals(True)
    for i in range(dlg._pv_list.count()):
        it = dlg._pv_list.item(i)
        on = it.data(Qt.ItemDataRole.UserRole) in checked
        it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
    dlg._pv_list.blockSignals(False)
    dlg._refresh_primary_combo()


def make_dialog(m, days, checked, windows=None):
    from PySide6.QtCore import QDate
    cams = [("C03-040-PTM11WNF-_-IMG", "PTM11WNF", Path("x"))]
    dlg = m.PVRegionSearchDialog(cams, [QDate(d.year, d.month, d.day) for d in days],
                                 windows=windows)
    set_ticks(dlg, checked)
    FETCHES.clear()
    dlg._reload_series()
    return dlg


def wait_all(dlg, day, checked, timeout_s=15.0):
    return B.wait_for(lambda: all(ch in (dlg._series.get(day) or {})
                                  for ch in checked), timeout_s=timeout_s)


def check_window(m):
    print("\n=== the graph reads only the picked time ===")
    ARCHIVE.clear()
    ARCHIVE[SBW4_CH] = [(ns_at(DAY, h), float(h)) for h in range(0, 24)]
    dlg = make_dialog(m, [DAY], (SBW4_CH,), windows={DAY: (7, 0, 21, 0)})
    wait_all(dlg, DAY, (SBW4_CH,))
    asked = [(s, e) for (ch, s, e) in FETCHES if ch == SBW4_CH]
    check("fetched 07:00–21:00, not the whole day",
          asked and asked[0] == (ns_at(DAY, 7), ns_at(DAY, 21)),
          repr([((s - ns_at(DAY, 0)) / 3.6e12, (e - ns_at(DAY, 0)) / 3.6e12)
                for s, e in asked]))
    lo, hi = dlg._ax.get_xlim()
    check("the time axis runs 07:00–21:00",
          abs(dlg._x_to_ns(lo) - ns_at(DAY, 7)) < 1e9
          and abs(dlg._x_to_ns(hi) - ns_at(DAY, 21)) < 1e9)
    def labels():
        fmt = dlg._ax.xaxis.get_major_formatter()
        return [fmt(x) for x in dlg._ax.xaxis.get_major_locator()()]
    got = labels()
    check("the time labels are whole hours from 07:00",
          got[:1] == ["07:00"] and all(s.endswith(":00") for s in got), repr(got))
    dlg._ax.set_xlim(dlg._ns_to_x(ns_at(DAY, 10), DAY),
                     dlg._ns_to_x(ns_at(DAY, 11), DAY))
    dlg._rethin_and_draw()
    got = labels()
    check("zoomed to one hour, the labels follow (finer steps)",
          len(got) >= 4 and got[0] == "10:00", repr(got))
    check("the config hands the window back to the search",
          dlg.get_config()["windows"].get(DAY) == (7, 0, 21, 0),
          repr(dlg.get_config()["windows"]))
    dlg.close(); dlg.deleteLater()

    dlg = make_dialog(m, [DAY], (SBW4_CH,), windows={DAY: (9, 30, 12, 0)})
    wait_all(dlg, DAY, (SBW4_CH,))
    asked = [(s, e) for (ch, s, e) in FETCHES if ch == SBW4_CH]
    check("a hand-picked 09:30–12:00 is what is read",
          asked and asked[0] == (ns_at(DAY, 9, 30), ns_at(DAY, 12)))
    dlg.close(); dlg.deleteLater()

    dlg = make_dialog(m, [DAY], (SBW4_CH,))
    wait_all(dlg, DAY, (SBW4_CH,))
    asked = [(s, e) for (ch, s, e) in FETCHES if ch == SBW4_CH]
    check("no window handed in → the tab's default 07–21",
          asked and asked[0] == (ns_at(DAY, 7), ns_at(DAY, 21)))
    dlg.close(); dlg.deleteLater()

    print("\n=== All days keeps to each day's window ===")
    ARCHIVE[SBW4_CH] += [(ns_at(DAY2, h), float(h)) for h in range(0, 24)]
    wins = {DAY: (7, 0, 21, 0), DAY2: (7, 0, 21, 0)}
    dlg = make_dialog(m, [DAY, DAY2], (SBW4_CH,), windows=wins)
    dlg._mode = "all"
    dlg._reload_series()
    wait_all(dlg, DAY2, (SBW4_CH,))
    dlg._redraw()
    t = ns_at(DAY2, 12, 34)
    back = dlg._x_to_ns(dlg._ns_to_x(t, DAY2))
    check("x ↔ time round trip on the second day", abs(back - t) < 1_000_000,
          f"{(back - t) / 1e6:.3f} ms")
    check("the second day starts at x = base + 1 (no night in between)",
          abs(dlg._ns_to_x(ns_at(DAY2, 7), DAY2) - (dlg._base_num() + 1)) < 1e-9)
    locs = list(dlg._ax.xaxis.get_major_locator()())
    fmt = dlg._ax.xaxis.get_major_formatter()
    labels = [fmt(x) for x in locs]
    check("the tick labels are clock times inside 07–21",
          labels and all("07:00" <= s <= "21:00" for s in labels)
          and labels[0] == "07:00", repr(labels))
    xs = dlg._drawn_lines[0][0].get_xdata()
    import numpy as np
    finite = np.asarray(xs)[np.isfinite(xs)]
    check("samples outside the window are not drawn (no spill into a neighbour)",
          len(finite) and finite.min() >= dlg._base_num() - 1e-9
          and finite.max() <= dlg._base_num() + 2 + 1e-9)
    dlg.close(); dlg.deleteLater()


def check_progressive(m):
    print("\n=== each PV is drawn as it arrives ===")
    ARCHIVE.clear()
    for ch, k in ((SBW4_CH, 1.0), (PTM1_CH, 2.0), (PCM2_CH, 3.0)):
        ARCHIVE[ch] = [(ns_at(DAY, 9) + i * 60_000_000_000, k) for i in range(60)]
    DELAY["s"] = 0.6
    try:
        order = (SBW4_CH, PTM1_CH, PCM2_CH)
        dlg = make_dialog(m, [DAY], order)
        got_first = B.wait_for(lambda: bool((dlg._series.get(DAY) or {})),
                               timeout_s=10.0)
        held = list((dlg._series.get(DAY) or {}))
        n_read = len(FETCHES)
        check("the first PV is on the graph before the last one is read",
              got_first and len(held) < 3 and n_read < 3,
              f"held {len(held)}, read {n_read}")
        check("…and it is drawn", len(dlg._drawn_lines) >= 1)
        check("the status line names what is loading next",
              "Loading" in dlg._status.text(), dlg._status.text())
        wait_all(dlg, DAY, order)
        B.wait_for(lambda: "Loaded" in dlg._status.text(), timeout_s=5.0)
        check("all three end up drawn", len(dlg._drawn_lines) == 3,
              str(len(dlg._drawn_lines)))
        check("and the load closes with the sample count",
              "Loaded" in dlg._status.text(), dlg._status.text())
        dlg.close(); dlg.deleteLater()
    finally:
        DELAY["s"] = 0.0


def check_big_day(m):
    print("\n=== a big day does not freeze the window ===")
    ARCHIVE.clear()
    n = 360_000
    t0 = ns_at(DAY, 7)
    step = (ns_at(DAY, 21) - t0) // n
    spike_i = 123_457
    ARCHIVE[SBW4_CH] = [(t0 + i * step, 1700.0 if i == spike_i else 0.0)
                        for i in range(n)]
    dlg = make_dialog(m, [DAY], (SBW4_CH,))
    wait_all(dlg, DAY, (SBW4_CH,), timeout_s=30.0)
    dlg._xy_cache.clear()
    a = time.perf_counter()
    dlg._redraw()
    cold = time.perf_counter() - a
    a = time.perf_counter()
    dlg._redraw()
    warm = time.perf_counter() - a
    check("360 000 samples redraw in under a second", cold < 1.0,
          f"{cold * 1000:.0f} ms (again: {warm * 1000:.0f} ms)")
    ln = dlg._drawn_lines[0][0]
    check("the drawn curve is thinned to a few thousand points",
          len(ln.get_xdata()) < 20_000, str(len(ln.get_xdata())))
    check("…and the single spike is still on it", max(ln.get_ydata()) == 1700.0,
          repr(max(ln.get_ydata())))
    # Zoom right in round the spike: the curve is re-thinned to what is shown.
    x_spike = dlg._ns_to_x(t0 + spike_i * step, DAY)
    dx = 30 * step / 86_400e9
    dlg._ax.set_xlim(x_spike - dx, x_spike + dx)
    dlg._rethin_lines()
    xs = ln.get_xdata()
    check("zoomed in, every sample in view is drawn again",
          50 <= len(xs) <= 70 and max(ln.get_ydata()) == 1700.0, str(len(xs)))
    dlg.close(); dlg.deleteLater()


def check_cond_row(m):
    print("\n=== one line per condition row ===")
    ARCHIVE.clear()
    dlg = make_dialog(m, [DAY], (SBW4_CH, PTM1_CH))
    wait_all(dlg, DAY, (SBW4_CH, PTM1_CH))
    dlg.resize(1520, 970)
    dlg.show()                         # offscreen platform: laid out, nothing on screen
    B.wait_for(lambda: False, timeout_s=0.3)
    row = dlg._cond_rows[SBW4_CH]
    box, op, v1 = row["box"], row["op"], row["v1"]
    yc = [w.mapTo(row["w"], w.rect().center()).y() for w in (box, op, v1)]
    check("name, test and value on one line", max(yc) - min(yc) <= 4, repr(yc))
    # Offscreen fonts are not real, so not a pixel count (on a real screen it is
    # 79 px beside a 163 px name): the box is sized to a number and does not
    # stretch, and the name takes what is left.
    check("the value box is sized to a number, not stretched",
          v1.minimumWidth() == v1.maximumWidth() == v1.width(),
          f"{v1.minimumWidth()}..{v1.maximumWidth()}, now {v1.width()}")
    check("the name takes the rest of the row",
          box.width() >= row["w"].width() - op.width() - v1.width() - 20,
          f"name {box.width()} of row {row['w'].width()}")
    check("the second value is hidden unless 'between'", not row["v2"].isVisible())
    op.setCurrentIndex(op.findData("between"))
    B.wait_for(lambda: False, timeout_s=0.1)
    check("'between' shows the second value", row["v2"].isVisible())
    check("…and the operator box fits the word",
          op.width() >= op.fontMetrics().horizontalAdvance("between") + 20)
    dlg.close(); dlg.deleteLater()


def main() -> int:
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(sys.argv)
    install_stubs(m)
    check_window(m)
    check_progressive(m)
    check_big_day(m)
    check_cond_row(m)
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
