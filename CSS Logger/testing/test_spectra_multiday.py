"""Spectra, end to end: pick two days, load them, draw them, select a spectrum.

test_spectra_windows.py pins the arithmetic. This one drives the real widget with
a fake archive, so it catches what the arithmetic cannot:

  1. every window is actually fetched, and the progress bar counts them all
  2. the sample every request returns from BEFORE its window is thrown away
  3. a PV with nothing in any window still says when it last recorded
  4. the graph really is drawn on the compressed axis, with a divider per join
  5. Axis limits refuses to edit X on the search graph but allows it below
  6. a click is not a spectrum; a drag across a divider is two

No network and no share — _fetch_scalars is replaced:

    python testing/test_spectra_multiday.py
"""
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
# Point APPDATA at a scratch folder BEFORE the widget is built, or the test
# rewrites the operator's own PV list and window geometry.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="spectra_multiday_test_")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from matplotlib.ticker import FuncFormatter                    # noqa: E402
from PySide6.QtCore import QPoint, Qt                          # noqa: E402
from PySide6.QtTest import QTest                                # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

import sp_t                                                    # noqa: E402
from sp_t import _TimeMap                                      # noqa: E402

dp = sp_t.daypicker
TZ = dp.TZ_PRAGUE

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


MON = date(2026, 8, 24)
WED = date(2026, 8, 26)


def ns(d: date, h: int, m: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=TZ).timestamp() * 1e9)


W_MON = (ns(MON, 8), ns(MON, 12))
W_WED = (ns(WED, 14), ns(WED, 19))
WINDOWS = [W_MON, W_WED]
H = 3600.0

DEAD_PV = "TEST:DEAD"                 # nothing in any window
LAST_SEEN = ns(date(2026, 8, 10), 9)  # ...but it recorded a fortnight ago

CALLS: "list[tuple[str, int, int]]" = []


def fake_fetch_scalars(channel: str, start_ns: int, end_ns: int):
    """Stand in for the archiver.

    Mimics the two things that bite: every request also hands back the last
    sample from before its own start, and a channel can be silent.
    """
    CALLS.append((channel, start_ns, end_ns))
    if channel == DEAD_PV:
        return [(LAST_SEEN, 0.5)]                       # only the freebie
    out = [(start_ns - 10 ** 9, -1.0)]                  # the freebie
    t = start_ns
    while t < end_ns:
        out.append((t, 1.0 + (t - start_ns) / 3.6e12))
        t += int(1800 * 1e9)                            # every half hour
    return out


def pump(app, w, limit: int = 600):
    """Spin the event loop until the background load has landed."""
    for _ in range(limit):
        app.processEvents()
        if not w._loading:
            return True
    return False


def build(app):
    sp_t._fetch_scalars = fake_fetch_scalars
    w = sp_t.SpectraWidget()
    w._search_pvs = [("Energy", "TEST:ENERGY"), ("Dead", DEAD_PV)]
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    return w


def test_load(app, w):
    CALLS.clear()
    w._segments = [dp.PickSeg(MON, 8, 0, 12, 0), dp.PickSeg(WED, 14, 0, 19, 0)]
    w._windows = list(WINDOWS)
    w._tmap = _TimeMap(WINDOWS)
    w._load_day_energy()
    check("the load finished", pump(app, w))

    check("every PV was asked for every window",
          len(CALLS) == 2 * len(WINDOWS), f"{len(CALLS)} requests")
    check("the requests are the picked windows, not whole days",
          sorted({(a, b) for _c, a, b in CALLS}) == sorted(WINDOWS))
    check("the progress bar counted PVs × windows",
          w._progress.maximum() == 2 * len(WINDOWS),
          str(w._progress.maximum()))

    live = {s["label"]: s for s in w._energy_data}
    check("both PVs came back", set(live) == {"Energy", "Dead"})
    e = live["Energy"]
    check("samples were kept", len(e["data"]) > 0, f"{len(e['data'])} samples")
    check("the sample from before each window was dropped",
          all(w._tmap.contains(t) for t, _v in e["data"]))
    check("no value from outside the windows survived",
          all(v >= 0 for _t, v in e["data"]))
    d = live["Dead"]
    check("a silent PV has no samples", not d["data"])
    check("a silent PV still says when it last recorded",
          d["last_before"] == LAST_SEEN, str(d["last_before"]))
    txt = w._lbl_status.text()
    check("the status no longer talks about 'this day'",
          "this day" not in txt, txt[:90])
    check("the status names the silent PV", "Dead" in txt, txt[:90])


def test_labels(w):
    check("the sidebar names both days",
          "2 days" in w._lbl_day.text(), w._lbl_day.text().replace("\n", " | "))
    check("the sidebar spells out the loaded windows in its tooltip",
          w._lbl_day.toolTip().count("2026-") == 2,
          w._lbl_day.toolTip().replace("\n", " | "))


def test_draw(app, w):
    w._draw_energy()
    app.processEvents()
    ax = w._ax_top
    lo, hi = ax.get_xlim()
    check("the graph is on the compressed axis (9 h wide, not 3 days)",
          abs((hi - lo) - 9 * H) < 300, f"{(hi - lo) / H:.2f} h")
    check("the x axis is formatted by us, not by matplotlib's dates",
          isinstance(ax.xaxis.get_major_formatter(), FuncFormatter))
    check("Axis limits is told this is a time axis",
          getattr(ax, "_sp_time_axis", False) is True)
    # One dashed divider per join. The graph's two overlay lines — the crosshair
    # and the shot bar's marker — live in ax.lines too, so they are excluded by
    # identity rather than by looks.
    overlay = [(w._top_cursor_artists or {}).get("vline")] + w._top_marker_list()
    dividers = [ln for ln in ax.lines
                if not any(ln is o for o in overlay) and len(ln.get_xdata()) == 2
                and ln.get_xdata()[0] == ln.get_xdata()[1]]
    check("one divider between the two days", len(dividers) == 1,
          f"{len(dividers)} found")
    check("the divider sits at the join",
          dividers and abs(float(dividers[0].get_xdata()[0]) - 4 * H) < 1,
          str([float(d.get_xdata()[0]) for d in dividers]))
    # The crosshair's readout annotations are empty until the mouse moves.
    labels = [t.get_text() for t in ax.texts if t.get_text()]
    check("each day is named on the graph", len(labels) == 2, str(labels))
    check("the names are the two picked days",
          all("24.08." in x or "26.08." in x for x in labels), str(labels))
    check("the tick labels read as clock times",
          any(":" in t.get_text() for t in ax.get_xticklabels()))


def test_axis_limits_dialog(app, w):
    top = sp_t._AxisLimitsDialog(w._ax_top, w._canvas_top)
    bot = sp_t._AxisLimitsDialog(w._ax_bot, w._canvas_bot)
    app.processEvents()
    check("Axis limits will not let X be typed on the search graph",
          top._e_xmin is None)
    check("...but still lets it be typed on the spectra graph",
          bot._e_xmin is not None)
    top.reject(); bot.reject()


def test_span(app, w):
    w._regions.clear()
    w._install_span()
    # A click, not a drag.
    w._on_span(1000.0, 1000.0 + 0.05)
    check("a click does not create a spectrum", len(w._regions) == 0,
          f"{len(w._regions)} created")
    # A drag inside Monday.
    w._on_span(1 * H, 2 * H)
    check("a drag inside one day creates one spectrum", len(w._regions) == 1)
    r = w._regions[0]
    check("it is stored in real time on the right day",
          sp_t._ns_to_dt(r["t_start"]).date() == MON
          and sp_t._ns_to_dt(r["t_start"]).hour == 9,
          str(sp_t._ns_to_dt(r["t_start"])))
    # A drag across the divider.
    w._regions.clear()
    w._on_span(3 * H, 5 * H)
    check("a drag across the divider creates one spectrum per day",
          len(w._regions) == 2, f"{len(w._regions)} created")
    days = {sp_t._ns_to_dt(r["t_start"]).date() for r in w._regions}
    check("one on each day", days == {MON, WED}, str(sorted(days)))
    check("neither covers time that was not selected",
          all(w._tmap.contains(r["t_start"])
              and w._tmap.contains(r["t_end"] - 1) for r in w._regions))
    txt = w._lbl_status.text()
    check("the operator is told why there are two", "2 spectra" in txt, txt[:100])


def test_drag_only_clipping_a_day(app, w):
    """Marking one day must not also mark the neighbour it overlapped by pixels.

    The whole of Monday (4 h) plus two minutes of Wednesday: the Wednesday piece
    is 0.8 % of the drag, so it is not a spectrum the operator asked for.
    """
    w._regions.clear()
    w._on_span(0.0, 4 * H + 120)
    check("a drag that only clips the next day marks one day",
          len(w._regions) == 1, f"{len(w._regions)} created")
    if w._regions:
        r = w._regions[0]
        check("the day kept is Monday, whole",
              sp_t._ns_to_dt(r["t_start"]).date() == MON
              and r["t_start"] == W_MON[0] and r["t_end"] == W_MON[1],
              f"{sp_t._ns_to_dt(r['t_start'])} → {sp_t._ns_to_dt(r['t_end'])}")
    txt = w._lbl_status.text()
    check("the operator is told the overhang was ignored",
          "ignored" in txt and "2026-08-26" in txt, txt[:140])

    # The mirror image: a clip on the LEFT of the intended day.
    w._regions.clear()
    w._on_span(4 * H - 120, 9 * H)
    check("a clip of the previous day is dropped too",
          len(w._regions) == 1, f"{len(w._regions)} created")
    if w._regions:
        r = w._regions[0]
        check("the day kept is Wednesday, whole",
              r["t_start"] == W_WED[0] and r["t_end"] == W_WED[1],
              f"{sp_t._ns_to_dt(r['t_start'])} → {sp_t._ns_to_dt(r['t_end'])}")

    # A day loaded with a SHORT window is a small share of the drag even when it
    # was selected in full — covering it whole must still count.
    short = (ns(MON, 8), ns(MON, 8, 30))
    w._regions.clear()
    w._tmap = _TimeMap([short, W_WED])
    w._on_span(0.0, 5.5 * H)
    check("selecting all of a short day counts, however small its share",
          len(w._regions) == 2, f"{len(w._regions)} created")
    w._tmap = _TimeMap(WINDOWS)
    w._regions.clear()


def test_dblclick_marks_whole_day(app, w):
    """Double-click inside a day → exactly that day, no aiming."""
    from matplotlib.backend_bases import MouseButton, MouseEvent

    w._regions.clear()
    w._draw_energy()
    app.processEvents()
    if hasattr(w, "_act_select"):
        w._act_select.setChecked(True)

    def dbl(x_axis: float, dbl_click: bool = True):
        """A double-click at x on the compressed axis, in real pixel coords.

        y comes from transAxes (mid-height), not from a data value — y = 0 can sit
        outside the Y limits, and then the pixel is outside the axes and the click
        never lands on the graph at all.
        """
        px = float(w._ax_top.transData.transform((x_axis, 0.0))[0])
        py = float(w._ax_top.transAxes.transform((0.0, 0.5))[1])
        evt = MouseEvent("button_press_event", w._canvas_top, px, py,
                         MouseButton.LEFT, dblclick=dbl_click)
        w._on_top_dblclick(evt)

    dbl(6 * H)                                   # somewhere inside Wednesday
    check("a double-click inside a day marks one spectrum",
          len(w._regions) == 1, f"{len(w._regions)} created")
    if w._regions:
        r = w._regions[0]
        check("it is that day's whole loaded window, edge to edge",
              (r["t_start"], r["t_end"]) == W_WED,
              f"{sp_t._ns_to_dt(r['t_start'])} → {sp_t._ns_to_dt(r['t_end'])}")
    txt = w._lbl_status.text()
    check("the status says the whole day was taken",
          "all of" in txt and "2026-08-26" in txt, txt[:120])

    dbl(6.5 * H)
    check("double-clicking the same day again adds no duplicate",
          len(w._regions) == 1, f"{len(w._regions)} created")
    check("...and says so instead", "already marked" in w._lbl_status.text(),
          w._lbl_status.text()[:120])

    dbl(2 * H)
    check("the other day can be added the same way",
          len(w._regions) == 2 and (w._regions[1]["t_start"],
                                    w._regions[1]["t_end"]) == W_MON,
          f"{len(w._regions)} regions")

    # A single click is not a selection, and neither is a double-click in the pad.
    n = len(w._regions)
    dbl(6 * H, dbl_click=False)
    check("a single click marks nothing", len(w._regions) == n)
    dbl(-0.5)
    check("a double-click off the days marks nothing", len(w._regions) == n,
          f"{len(w._regions)} regions")

    # A second search PV puts a twinx axis over the graph, and THAT is the axis
    # the click reports. The handler must accept it, or the whole feature dies as
    # soon as two PVs are ticked. _draw_energy rebuilds the twins, so the one
    # added here does not outlive this check.
    w._regions.clear()
    twin = w._ax_top.twinx()
    w._top_extra_axes.append(twin)

    class _Evt:                       # only what the handler reads
        dblclick = True
        button = MouseButton.LEFT
        inaxes = twin

    e = _Evt()
    e.x = float(w._ax_top.transData.transform((6 * H, 0.0))[0])
    e.y = float(w._ax_top.transAxes.transform((0.0, 0.5))[1])
    w._on_top_dblclick(e)
    check("a double-click reported on a twin Y-axis still marks the day",
          len(w._regions) == 1
          and (w._regions[0]["t_start"], w._regions[0]["t_end"]) == W_WED,
          f"{len(w._regions)} regions")

    # Everything above calls the handler directly. This one goes through Qt: a
    # real double-click on the canvas, which is what proves matplotlib forwards it
    # at all (right-click, for instance, it does not — see _make_canvas_panel).
    w._regions.clear()
    w._draw_energy()
    app.processEvents()
    canvas = w._canvas_top
    ratio  = getattr(canvas, "device_pixel_ratio", 1) or 1
    px = float(w._ax_top.transData.transform((6 * H, 0.0))[0])
    py = float(w._ax_top.transAxes.transform((0.0, 0.5))[1])
    qt_pt = QPoint(int(px / ratio), int((canvas.figure.bbox.height - py) / ratio))
    QTest.mouseDClick(canvas, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      qt_pt)
    app.processEvents()
    check("a real Qt double-click on the canvas reaches the handler",
          len(w._regions) == 1
          and (w._regions[0]["t_start"], w._regions[0]["t_end"]) == W_WED,
          f"{len(w._regions)} regions at {qt_pt.x()},{qt_pt.y()}")
    w._regions.clear()


def test_stale_zoom_is_dropped(app, w):
    """A zoom is stored in axis coordinates, so it must never outlive a reload.

    On the compressed axis "x = 100" stands for a different instant as soon as
    the windows change, so a remembered zoom would silently show the wrong data.
    A full load always resets the view — this pins that.
    """
    w._top_user_xlim = (100.0, 200.0)
    w._top_user_ylim = (0.0, 1.0)
    w._load_day_energy()
    check("the load finished", pump(app, w))
    check("a reload drops the remembered zoom",
          w._top_user_xlim is None and w._top_user_ylim is None,
          f"x={w._top_user_xlim} y={w._top_user_ylim}")
    lo, hi = w._ax_top.get_xlim()
    check("and the view is the whole selection again",
          abs((hi - lo) - 9 * H) < 300, f"{(hi - lo) / H:.2f} h")


def main() -> int:
    print("test_spectra_multiday")
    app = QApplication.instance() or QApplication(sys.argv)
    w = build(app)
    for fn, args in ((test_load, (app, w)), (test_labels, (w,)),
                     (test_draw, (app, w)), (test_axis_limits_dialog, (app, w)),
                     (test_span, (app, w)),
                     (test_drag_only_clipping_a_day, (app, w)),
                     (test_dblclick_marks_whole_day, (app, w)),
                     (test_stale_zoom_is_dropped, (app, w))):
        print(f"\n{fn.__name__}")
        fn(*args)
    w.close()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
