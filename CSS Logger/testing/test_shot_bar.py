"""Spectra tab — the shot bar under the search graph, and its two rules.

  RULE 1 — the bar and the graph share one X.
    The handle's centre pixel and the shot's pixel in the graph are the SAME
    pixel. Measured on a really shown window, for every shot, and again after a
    resize, after a splitter drag, with a second search PV putting a Y axis on
    the right, and while zoomed in.

  RULE 2 — the bar can only stand on a real measurement.
    Sweeping the bar across its whole travel, every place it comes to rest is a
    shot that exists. It never rests in the empty stretch between two selections.

Plus the trap that made this worth testing: turning a time into a bar position
and back TRUNCATES, so an "at or before" lookup names the shot BEFORE the one
the handle was put on — every time, in the same direction.

What the bar picks, lists and labels is tested in test_single_browser.py.

Renders testing/_out/shot_bar_*.png so the bar can be looked at.

Run:  python testing/test_shot_bar.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

import sp_t
from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

NX = 128
X = np.linspace(780.0, 840.0, NX)
T0 = int(1_756_000_000 * 1e9)          # a fixed wall-clock start, in ns
SEC = 10 ** 9
HOUR = 3600 * SEC
DAY = 24 * HOUR

_failed = False


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


def _region(rid: int, colour: str, times):
    n = len(times)
    rng = np.random.default_rng(rid)
    c = 806.0 + rng.normal(0.0, 1.0, n)
    st = np.exp(-0.5 * ((X[None, :] - c[:, None]) / 8.0) ** 2)
    return {
        "id": rid, "t_start": min(times), "t_end": max(times),
        "color": colour, "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True,
        "x": X, "stack": st, "stack_ts": list(times),
        "mean": st.mean(axis=0), "median": np.median(st, axis=0),
        "trimmed": st.mean(axis=0), "sigma": st.mean(axis=0),
        "std": st.std(axis=0),
        "p10": np.percentile(st, 10, axis=0),
        "p90": np.percentile(st, 90, axis=0),
        "orders": {"GDD": 1234.0, "TOD": -5678.0, "FOD": 9.0},
        "energy_avg": 9.87, "energy_n": n, "n": n,
    }


def _widget():
    w = SpectraWidget()
    w._spec_base_pv = "L3-SBW4-SPEC:Spectrum"
    w._spec_x_pv = w._spec_base_pv + "_X"
    w._spec_y_pv = w._spec_base_pv + "_Y"
    w._x_axis_cfg = {"mode": "native"}
    w._x_data = X
    w._sb_x_min.setValue(780)
    w._sb_x_max.setValue(840)
    w._chk_autofit.setChecked(False)
    w.resize(1500, 900)
    # Really shown, not just built: the alignment is a claim about pixels, and an
    # unshown widget has no geometry to measure.
    w.show()
    QApplication.processEvents()
    return w


def _load(w, windows, regions):
    w._windows = list(windows)
    w._tmap = sp_t._TimeMap(windows)
    w._regions = list(regions)
    w._region_seq = max(r["id"] for r in regions)
    w._rebuild_regions_ui()
    i = w._cmb_method.findText("Every spectrum")
    w._cmb_method.setCurrentIndex(i)
    w._redraw_spectra()
    w._ax_top.set_xlim(*w._tmap.xlim())
    _settle(w)


def _settle(w):
    """Let the search graph draw, and the deferred pin that follows it run."""
    for _ in range(4):
        w._canvas_top.draw()
        QApplication.processEvents()


# ── rule 1: the same X on the graph is the same X on the bar ──────────────────

def _handle_x_global(w) -> float:
    """The centre of the bar's handle, in screen pixels."""
    sl = w._sl_shot
    return sl.mapToGlobal(QPoint(0, 0)).x() + sl.pixel_at_value(sl.value())


def _data_x_global(w, ts: int) -> float:
    """Where that instant is drawn in the search graph, in screen pixels."""
    x = w._tmap.to_x_clamped(int(ts))
    x_disp = float(w._ax_top.transData.transform((x, 0.0))[0])
    ratio = getattr(w._canvas_top, "device_pixel_ratio", 1) or 1
    return w._canvas_top.mapToGlobal(QPoint(0, 0)).x() + x_disp / ratio


def _worst_offset(w) -> "tuple[float, int]":
    """The biggest gap between handle and data point over every shot."""
    worst, at = 0.0, -1
    for i, it in enumerate(w._single_items_cache):
        w._go_to_shot(i)
        QApplication.processEvents()
        d = abs(_handle_x_global(w) - _data_x_global(w, it["ts"]))
        if d > worst:
            worst, at = d, i
    return worst, at


def test_alignment(w):
    print("\ntest_alignment")
    t1 = [T0 + i * 120 * SEC for i in range(40)]
    t2 = [T0 + DAY + i * 90 * SEC for i in range(30)]
    _load(w, [(T0 - HOUR, T0 + 3 * HOUR), (T0 + DAY - HOUR, T0 + DAY + 3 * HOUR)],
          [_region(1, "#1565C0", t1), _region(2, "#C62828", t2)])
    _ok(len(w._single_items_cache) == 70, "70 shots over two days",
        str(len(w._single_items_cache)))

    worst, at = _worst_offset(w)
    _ok(worst <= 1.0, "every shot: the handle is on the data point's own pixel",
        f"worst {worst:.2f} px at shot {at + 1}")

    # The plot box moves on a resize, so the bar has to be re-pinned.
    for wide in (1100, 1900, 1500):
        w.resize(wide, 900)
        _settle(w)
        worst, at = _worst_offset(w)
        _ok(worst <= 1.0, f"still on the pixel at {wide} px wide",
            f"worst {worst:.2f} px")

    # A dragged splitter changes the graph's height, and with it the room
    # tight_layout gives the tick labels — the left edge of the plot box moves.
    w._splitter.setSizes([200, 700])
    _settle(w)
    worst, _at = _worst_offset(w)
    _ok(worst <= 1.0, "still on the pixel after the divider is dragged",
        f"worst {worst:.2f} px")
    w._splitter.setSizes([440, 320])
    _settle(w)

    # Zoomed in, the bar covers only what the graph shows — and still lines up.
    # Zoomed onto the middle of the first selection, not onto a fraction of the
    # axis: a fraction of a two-day axis can easily land in the removed time.
    centre = w._tmap.to_x_clamped(t1[len(t1) // 2])
    w._ax_top.set_xlim(centre - 600.0, centre + 600.0)
    _settle(w)
    x0, x1 = w._ax_top.get_xlim()
    inside = [i for i, it in enumerate(w._single_items_cache)
              if x0 <= w._tmap.to_x_clamped(it["ts"]) <= x1]
    _ok(len(inside) > 3, "the zoom leaves a handful of shots on screen",
        str(len(inside)))
    worst, at = 0.0, -1
    for i in inside:
        w._go_to_shot(i)
        QApplication.processEvents()
        d = abs(_handle_x_global(w) - _data_x_global(w, w._single_items_cache[i]["ts"]))
        if d > worst:
            worst, at = d, i
    _ok(worst <= 1.0, "zoomed in, the handle is still on the data point's pixel",
        f"worst {worst:.2f} px")

    # Stepping onto a shot that is off screen slides the graph over instead of
    # leaving the handle stuck against the edge.
    w._go_to_shot(inside[-1])
    QApplication.processEvents()
    before = w._ax_top.get_xlim()
    w._step_single(+3)
    _settle(w)
    after = w._ax_top.get_xlim()
    it = w._single_items_cache[w._single_pos]
    _ok(after != before, "stepping past the edge pans the graph",
        f"{before[0]:.0f}..{before[1]:.0f} -> {after[0]:.0f}..{after[1]:.0f}")
    _ok(abs((after[1] - after[0]) - (before[1] - before[0])) < 1e-6,
        "and keeps the zoom width")
    _ok(abs(_handle_x_global(w) - _data_x_global(w, it["ts"])) <= 1.0,
        "the handle came along with it")

    # Back to the whole axis. set_xlim, not the toolbar's Home: this fixture never
    # went through _draw_energy, so the toolbar's stack of views still remembers
    # the placeholder graph and Home would put those limits back.
    w._ax_top.set_xlim(*w._tmap.xlim())
    _settle(w)
    worst, _at = _worst_offset(w)
    _ok(worst <= 1.0, "zooming back out puts the bar over the whole axis again",
        f"worst {worst:.2f} px")

    shot = w._top_container.grab()
    shot.save(os.path.join(OUT, "shot_bar_aligned.png"))


# ── rule 2: only on a real measurement ────────────────────────────────────────

def test_snaps_to_real_shots(w):
    print("\ntest_snaps_to_real_shots")
    # Two selections an hour apart inside one loaded day, so there is a wide
    # stretch of axis with nothing measured on it.
    t1 = [T0 + i * 20 * SEC for i in range(25)]
    t2 = [T0 + 2 * HOUR + i * 20 * SEC for i in range(25)]
    _load(w, [(T0 - 600 * SEC, T0 + 3 * HOUR)],
          [_region(1, "#1565C0", t1), _region(2, "#C62828", t2)])

    every_ts = sorted(t1 + t2)
    want = {w._shot_bar_value_from_ts(t) for t in every_ts}

    rest = set()
    for v in range(0, sp_t._SHOT_BAR_MAX + 1, 97):
        w._sl_shot.setValue(v)
        QApplication.processEvents()
        rest.add(w._sl_shot.value())
    _ok(rest <= want, "the bar only ever comes to rest on a measured shot",
        f"{len(rest)} places, all of them shots" if rest <= want
        else f"{len(rest - want)} of {len(rest)} are not shots")

    # And nothing rests in the hole between the two selections.
    gap_lo, gap_hi = max(t1), min(t2)
    in_gap = [v for v in rest
              if gap_lo < w._shot_bar_ts_from_value(v) < gap_hi
              and min(abs(w._shot_bar_ts_from_value(v) - t) for t in every_ts) > 20 * SEC]
    _ok(not in_gap, "and never in the hour where nothing was measured",
        f"{len(in_gap)} did")

    # Both ends of the hole are reachable: dragging in from the left stops on the
    # last shot before it, dragging in from the right on the first shot after it.
    mid_ts = (gap_lo + gap_hi) // 2
    w._sl_shot.setValue(w._shot_bar_value_from_ts(mid_ts - 60 * SEC))
    QApplication.processEvents()
    _ok(w._single_items_cache[w._single_pos]["ts"] == gap_lo,
        "coming from the left it stops at the last shot before the hole")
    w._sl_shot.setValue(w._shot_bar_value_from_ts(mid_ts + 60 * SEC))
    QApplication.processEvents()
    _ok(w._single_items_cache[w._single_pos]["ts"] == gap_hi,
        "coming from the right it stops at the first shot after it")


def test_nearest_not_previous(w):
    print("\ntest_nearest_not_previous")
    # A short window, so one bar unit is a fraction of the gap between shots and
    # each one really is reachable on its own.
    times = [T0 + int(i * 0.3 * SEC) for i in range(60)]     # 3.3 shots a second
    _load(w, [(T0 - 5 * SEC, T0 + 25 * SEC)], [_region(1, "#1565C0", times)])
    _ok(len(w._single_items_cache) == 60, "60 shots at 3.3 a second",
        str(len(w._single_items_cache)))

    off = []
    for i, it in enumerate(w._single_items_cache):
        v = w._shot_bar_value_from_ts(it["ts"])
        w._sl_shot.setValue(v)
        QApplication.processEvents()
        if w._single_pos != i:
            off.append((i, w._single_pos))
    _ok(not off, "put on a shot's own place, the bar names THAT shot",
        f"{len(off)} landed elsewhere, e.g. {off[:3]}" if off else "all 60")

    # The trap has a direction: a truncating lookup is always one shot early.
    early = [a for a, b in off if b == a - 1]
    _ok(not early, "not one of them is the shot before it",
        f"{len(early)} were" if early else "")

    # Stepping is not affected by how fine the bar is: it walks the list itself.
    w._go_to_shot(0)
    for _ in range(5):
        w._step_single(+1)
        QApplication.processEvents()
    _ok(w._single_pos == 5, "five presses of ▶ move five shots", str(w._single_pos))
    w._page_single(-1)
    QApplication.processEvents()
    _ok(w._single_pos == 2, "PageDown moves a twentieth of the list",
        str(w._single_pos))


def test_real_mouse(w):
    print("\ntest_real_mouse")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    times = [T0 + i * 30 * SEC for i in range(80)]
    _load(w, [(T0 - 600 * SEC, T0 + 3 * HOUR)], [_region(1, "#1565C0", times)])
    sl = w._sl_shot

    # A click on the bar goes STRAIGHT there. Qt's own default would page a step
    # towards it, which on a whole day would take a dozen clicks.
    w._go_to_shot(0)
    QApplication.processEvents()
    target = w._single_items_cache[55]["ts"]
    px = int(round(sl.pixel_at_value(w._shot_bar_value_from_ts(target))))
    QTest.mouseClick(sl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(px, sl.height() // 2))
    QApplication.processEvents()
    _ok(w._single_items_cache[w._single_pos]["ts"] == target,
        "a click lands on the shot drawn at that pixel", f"pos {w._single_pos + 1}")

    # And it lands on the shot under the CLICKED pixel, not one along.
    _ok(abs(_handle_x_global(w) - _data_x_global(w, target)) <= 1.0,
        "and the handle ends up on that shot's pixel")

    # Dragging keeps following the mouse.
    seen = []
    QTest.mousePress(sl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(px, sl.height() // 2))
    for step in range(0, -220, -20):
        QTest.mouseMove(sl, QPoint(px + step, sl.height() // 2))
        QApplication.processEvents()
        seen.append(w._single_pos)
    QTest.mouseRelease(sl, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       QPoint(px - 200, sl.height() // 2))
    QApplication.processEvents()
    _ok(seen == sorted(seen, reverse=True) and seen[0] > seen[-1],
        "dragging left walks steadily back through the shots",
        f"{seen[0] + 1} -> {seen[-1] + 1}")

    # Dragging must never re-lay-out either graph: three thousand curves take
    # seconds to draw, so a drag has to be blits only.
    calls = {"top": 0, "bot": 0}
    real_top, real_bot = w._draw_energy, w._redraw_spectra

    def spy_top(*a, **k):
        calls["top"] += 1
        return real_top(*a, **k)

    def spy_bot(*a, **k):
        calls["bot"] += 1
        return real_bot(*a, **k)

    w._draw_energy, w._redraw_spectra = spy_top, spy_bot
    try:
        QTest.mousePress(sl, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, QPoint(px, sl.height() // 2))
        for step in range(0, 240, 12):
            QTest.mouseMove(sl, QPoint(px - 200 + step, sl.height() // 2))
            QApplication.processEvents()
        QTest.mouseRelease(sl, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier,
                           QPoint(px + 40, sl.height() // 2))
        QApplication.processEvents()
    finally:
        w._draw_energy, w._redraw_spectra = real_top, real_bot
    _ok(calls == {"top": 0, "bot": 0}, "a whole drag redraws neither graph",
        f"search {calls['top']}, spectra {calls['bot']}")


def test_marker_follows_the_bar(w):
    print("\ntest_marker_follows_the_bar")
    times = [T0 + i * 60 * SEC for i in range(20)]
    _load(w, [(T0 - 600 * SEC, T0 + 3 * HOUR)], [_region(1, "#1565C0", times)])
    line, tag = w._top_marker_list()
    w._go_to_shot(7)
    QApplication.processEvents()
    _ok(line.get_visible() and tag.get_visible(), "the search graph is marked")
    _ok(abs(line.get_xdata()[0] - w._tmap.to_x_clamped(times[7])) < 1e-6,
        "on the shot's own place on the time axis")
    _ok(sp_t._fmt_hms(times[7]) in tag.get_text(), "and the tag names its time",
        tag.get_text().strip())
    ec = tag.get_bbox_patch().get_edgecolor()[:3]
    _ok(ec == (0x15 / 255, 0x65 / 255, 0xC0 / 255),
        "in the selection's own colour", str(ec))

    w._chk_single_hl.setChecked(False)
    QApplication.processEvents()
    _ok(not w._top_marker_list()[0].get_visible(),
        "unticking Highlight takes the marker off too")
    w._chk_single_hl.setChecked(True)
    QApplication.processEvents()

    # A full redraw of the search graph must leave the marker on it: it is an
    # animated artist, so it is left out of the draw and put back by the blit.
    w._canvas_top.draw()
    QApplication.processEvents()
    _ok(w._top_marker_list()[0].get_visible(), "and a redraw does not lose it")

    shot = w._top_container.grab()
    shot.save(os.path.join(OUT, "shot_bar_marker.png"))


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    w = _widget()
    test_alignment(w)
    test_snaps_to_real_shots(w)
    test_nearest_not_previous(w)
    test_real_mouse(w)
    test_marker_follows_the_bar(w)
    w.close()
    w.deleteLater()
    print("\nOutput in", OUT)
    print("RESULT:", "FAILED" if _failed else "OK")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
