"""Pointing graph — time colour bar, over-shots trend, near-field mm, tab names
(05.10.2026).

What the user asked for, and what is checked here (in process, offscreen, no window
on screen):

  * the colour bar of the beam path, X-Y by time and 3D carries real times (5-7
    ticks, the day too over a multi-day run) instead of Start / Mid / End;
  * its pointer (triangle + exact time) sits LEFT of the bar and never on a
    graph, the tick labels stay inside the canvas;
  * the 3D view has the same slider: a press on the bar is taken by the slider
    (not by matplotlib's turn), a drag walks the shots and loads the frame, the
    ring sits on the projected point (06.10.2026: the lime dot is gone);
  * X, Y over shots: a run drifting up and left shows a falling X trend and a
    rising Y trend (mm), the inverted px axis the same way up; both graphs have
    a shot axis with numbers;
  * near field: mm = px × pixel / M;
  * a new Image Slider tab is called "Panel N"; old automatic "Tab N" names are
    renamed, typed names kept.

    PYTHONIOENCODING=utf-8 python testing/test_pointing_time_bar_and_trend.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_ptbar_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                                    # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt                        # noqa: E402
from PySide6.QtGui import QMouseEvent                                 # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                   # noqa: E402

import is_t                                                           # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def mouse(panel, kind, button, x, y):
    c = panel._canvas
    local = QPointF(x, y)
    ev = QMouseEvent(kind, local, QPointF(c.mapToGlobal(local.toPoint())),
                     button, button if kind != QEvent.Type.MouseButtonRelease
                     else Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    return panel.eventFilter(c, ev)


def tick_labels(panel):
    return [t.get_text() for t in panel._cbar_ax.get_yticklabels()]


def layout_ok(panel, graph_ax, what):
    """Pointer zone left of the bar is free of the graph; ticks inside canvas."""
    w = panel._canvas.width()
    x0, _, x1, _ = panel._cbar_rect_px()
    g_right = graph_ax.get_position().x1 * w
    check(f"{what}: the graph ends before the pointer strip",
          g_right <= x0 - panel._tb_left + 1, f"graph {g_right:.0f} bar {x0:.0f}")
    lbl = panel._cbar_time_lbl
    check(f"{what}: the time readout sits between graph and bar",
          lbl.isVisible() and lbl.x() >= g_right - 1 and lbl.x() + lbl.width() <= x0,
          f"label {lbl.x()}..{lbl.x() + lbl.width()} graph {g_right:.0f} bar {x0:.0f}")
    knob = panel._cbar_handle
    mid = knob.x() + knob.width() / 2.0
    check(f"{what}: the round knob sits on the bar",
          knob.isVisible() and abs(mid - (x0 + x1) / 2.0) <= 1.5,
          f"knob centre {mid:.1f} bar {x0:.1f}..{x1:.1f}")
    check(f"{what}: the time readout ends left of the knob",
          lbl.x() + lbl.width() <= knob.x(),
          f"label ends {lbl.x() + lbl.width()} knob {knob.x()}")
    r = panel._fig.canvas.get_renderer()
    dpr = float(getattr(panel._canvas, "device_pixel_ratio", 1.0) or 1.0)
    right = max(t.get_window_extent(r).x1 for t in panel._cbar_ax.get_yticklabels()) / dpr
    check(f"{what}: tick labels inside the canvas", right <= w, f"{right:.0f} > {w}")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # ── Ticks ────────────────────────────────────────────────────────────────
    t0 = 1_759_650_000 * 1_000_000_000          # 2025-10-05 09:40 Prague
    fr, lb = is_t.pointing_time_ticks(t0, t0 + 6 * 3600 * 10**9, 7, False)
    check("7 ticks from the first shot to the last", len(lb) == 7
          and fr[0] == 0.0 and fr[-1] == 1.0, f"{lb}")
    check("first tick = first shot's HH:MM",
          lb[0] == is_t.fmt_prague_full_from_ns(t0)[11:16], f"{lb[0]}")
    _, lb2 = is_t.pointing_time_ticks(t0, t0 + 300 * 10**9, 5, False)
    check("a 5-minute run shows seconds", len(lb2[0]) == 8, f"{lb2}")
    _, lb3 = is_t.pointing_time_ticks(t0, t0 + 2 * 86400 * 10**9, 7, True)
    check("multi-day: the first tick carries the day", lb3[0][2] == "-", f"{lb3}")
    check("multi-day: a tick on a new day carries it too",
          sum(1 for s in lb3 if len(s) > 5) >= 3, f"{lb3}")

    # ── Pauses on the X-Y drift strip ────────────────────────────────────────
    gaps = np.full(300, 5 * 10**9, dtype=np.int64)
    gaps[40] = 240 * 10**9
    gaps[200] = 15 * 10**9                      # a short hiccup is not a pause
    pts = t0 + np.cumsum(gaps)
    check("a 4-minute break is a pause, a 15 s one is not",
          is_t.pointing_pause_after(pts) == [39], f"{is_t.pointing_pause_after(pts)}")
    check("an even run has no pause",
          is_t.pointing_pause_after(t0 + np.arange(300) * 5 * 10**9) == [])

    # ── Data: a run drifting up and to the left on the chip ─────────────────
    panel = is_t.PointingPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.resize(1000, 520)
    rng = np.random.default_rng(7)
    n = 800
    drift = np.linspace(0.0, 1.0, n)
    cx = -6.0 * drift + rng.normal(0, 4.0, n)      # left = smaller px column
    cy = -8.0 * drift + rng.normal(0, 4.0, n)      # up = smaller px row
    ts = t0 + np.arange(n, dtype=np.int64) * 5 * 10**9
    panel.plot(cx, cy, n, ts_ns=ts.astype(np.float64), ts_ns_int=ts,
               img_w=1280, img_h=1024)
    panel.show()
    app.processEvents()
    got = []
    panel.point_clicked.connect(got.append)

    # Over shots, mm.
    panel.set_unit("mm")
    panel.set_view("time")
    app.processEvents()
    ax_x, ax_y = panel._fig.get_axes()[:2]
    tx = ax_x.get_lines()[1].get_ydata()
    ty = ax_y.get_lines()[1].get_ydata()
    check("over shots: there is a trend line", len(tx) == n and len(ty) == n)
    check("mm: X trend falls (beam went left)", tx[-50:].mean() < tx[:50].mean(),
          f"{tx[:50].mean():.4f} → {tx[-50:].mean():.4f}")
    check("mm: Y trend rises (beam went up)", ty[-50:].mean() > ty[:50].mean(),
          f"{ty[:50].mean():.4f} → {ty[-50:].mean():.4f}")
    check("both graphs show shot numbers",
          ax_x.xaxis.get_tick_params()["labelbottom"] is not False
          and ax_x.get_xlabel().startswith("Shot N.")
          and any(t.get_text() for t in ax_x.get_xticklabels()))
    panel.set_unit("px")
    app.processEvents()
    ax_y = panel._fig.get_axes()[1]
    ty = ax_y.get_lines()[1].get_ydata()
    yl = ax_y.get_ylim()
    check("px: Y trend falls in rows and the axis is turned (still drawn upwards)",
          ty[-50:].mean() < ty[:50].mean() and yl[0] > yl[1], f"{yl}")
    panel.set_unit("mm")

    # X-Y by time: the bar with times beside the cloud.
    panel.set_view("xy_time")
    app.processEvents()
    check("X-Y by time: a time bar is drawn", panel._cbar_ax is not None)
    labels = tick_labels(panel)
    check("X-Y by time: ticks are times, no Start/End",
          len(labels) in (5, 7) and all(":" in s for s in labels), f"{labels}")
    layout_ok(panel, panel._fig.get_axes()[2], "X-Y by time")
    check("X-Y by time: the title no longer explains the colours",
          "blue" not in panel._fig.get_axes()[0].get_title())

    # Beam path.
    panel.toggle_path()
    app.processEvents()
    check("beam path: a time bar is drawn", panel._cbar_ax is not None)
    layout_ok(panel, panel._path_ax, "beam path")

    # 3D: the slider.
    panel.toggle_path()
    panel.set_view("3d")
    app.processEvents()
    check("3D: a time bar is drawn", panel._cbar_ax is not None)
    ax3 = [a for a in panel._fig.get_axes() if getattr(a, "name", "") == "3d"][0]
    layout_ok(panel, ax3, "3D")
    x0, yt, x1, yb = panel._cbar_rect_px()
    got.clear()
    mid_y = (yt + yb) / 2.0
    took = mouse(panel, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
                 (x0 + x1) / 2, mid_y)
    check("3D: a press on the bar is the slider's", took is True)
    mouse(panel, QEvent.Type.MouseMove, Qt.MouseButton.LeftButton, (x0 + x1) / 2,
          yt + 0.25 * (yb - yt))
    mouse(panel, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton,
          (x0 + x1) / 2, yt + 0.25 * (yb - yt))
    k = panel._tc_k
    check("3D: the drag lands on a shot ~75 % into the run and loads it",
          got and got[-1] == k and abs(k - 0.75 * (n - 1)) < n * 0.03, f"{got[-3:]} k={k}")
    from mpl_toolkits.mplot3d import proj3d
    xs, ys, _ = proj3d.proj_transform(float(panel._cx[k]), float(panel._cy[k]),
                                      float(k + 1), ax3.get_proj())
    qx, qy = panel._data_to_qt(ax3, float(xs), float(ys))
    dot = panel._cur_ring2
    check("3D: the ring sits on that point",
          dot.isVisible() and abs(dot.x() + dot.width() / 2 - qx) <= 1.5
          and abs(dot.y() + dot.height() / 2 - qy) <= 1.5,
          f"ring {dot.x()},{dot.y()} point {qx:.0f},{qy:.0f}")
    check("3D: no lime dot any more", not hasattr(panel, "_path_cursor"))
    check("3D: a press off the bar is still matplotlib's (turning)",
          mouse(panel, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
                200, 250) is False)
    mouse(panel, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton, 200, 250)
    ax3.view_init(elev=10, azim=-20)
    panel._draw()
    ax3b = [a for a in panel._fig.get_axes() if getattr(a, "name", "") == "3d"][0]
    check("3D: a redraw keeps the turn", abs(ax3b.elev - 10) < 1e-6
          and abs(ax3b.azim + 20) < 1e-6, f"{ax3b.elev} {ax3b.azim}")

    # Multi-day: the bar labels carry the day.
    ts2 = t0 + np.arange(n, dtype=np.int64) * 300 * 10**9      # 800 × 5 min ≈ 2.8 d
    panel.plot(cx, cy, n, ts_ns=ts2.astype(np.float64), ts_ns_int=ts2,
               img_w=1280, img_h=1024)
    panel.set_view("xy_time")
    app.processEvents()
    labels = tick_labels(panel)
    check("multi-day bar: ticks carry the day", sum(1 for s in labels if "-" in s) >= 2,
          f"{labels}")
    check("multi-day readout: day on its own line",
          "\n" in panel._cbar_time_lbl.text(), repr(panel._cbar_time_lbl.text()))
    layout_ok(panel, panel._fig.get_axes()[2], "multi-day X-Y by time")
    panel.resize(620, 480)
    app.processEvents()
    panel._relayout_if_needed()
    app.processEvents()
    layout_ok(panel, panel._fig.get_axes()[2], "narrow X-Y by time")

    # ── Near field ───────────────────────────────────────────────────────────
    check("far field: mm = px × pixel", abs(is_t.pointing_mm_per_px(4.5) - 0.0045) < 1e-12)
    check("near field: mm = px × pixel / M",
          abs(is_t.pointing_mm_per_px(4.5, 1 / 66) - 0.0045 * 66) < 1e-12)

    # ── Slider tab names ─────────────────────────────────────────────────────
    import slider_tabs
    p = _TMP / "slider_tabs.json"
    p.write_text(json.dumps({"current": 0, "tabs": [
        {"name": "Tab 1"}, {"name": "Laser running"}, {"name": "Tab2"}]}),
        encoding="utf-8")
    slider_tabs.TABS_PATH = p

    class _Dummy(QWidget):
        pass

    st = slider_tabs.SliderTabs(_Dummy)
    names = [st._tabs.tabText(i) for i in range(st._tabs.count())]
    check("old automatic names become Panel N, typed ones stay",
          names == ["Panel 1", "Laser running", "Panel 2"], f"{names}")
    check("the next new tab is Panel 3", st._next_name() == "Panel 3", st._next_name())

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f_ in FAILURES:
        print(f"  - {f_}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
