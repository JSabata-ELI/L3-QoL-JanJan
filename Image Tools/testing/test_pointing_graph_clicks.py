"""Pointing graph — clicks, beam-path zoom and the px / µrad switch (01.10.2026).

The user reported that clicking a point no longer loaded its frame. The cause was
outside the click code: every ordinary navigation (slider, arrows) told the graph to
hide every point later than the frame on screen, so after one step most of the
cloud — and everything a click could hit — was gone.

What is checked, driving the graph panel in process (events are handed straight to
its own event filter; no desktop input, no window on screen):

  * a click on a point emits that point; a click on empty space emits nothing;
  * the clicked point gets the ring;
  * a click on the beam path emits the nearest path sample;
  * the wheel over the beam path zooms the path and leaves the cloud alone, and
    a redraw keeps that zoom; double-click on the path undoes it;
  * px → µrad redraws the same points ×0.227 with Y turned up, carries the zoom
    over, and µrad → px gives the original view back;
  * set_current_ts marks the matching point and nothing for an unknown frame;
  * (0, 0) is the median of the shots kept, and moves (with the zoom) when shots
    are deleted, undone or restored (05.10.2026);
  * new optics (Pixel / M / f) rescale the µrad points and the zoom;
  * mm (= px × pixel size, Y up) shows the same points, carries the zoom over
    from µrad, and a new pixel size rescales it (05.10.2026);
  * the "X–Y by time", "X, Y over shots" and "3D" views draw, and only the X–Y
    views take clicks and Delete mode; M parses "1/66", "0,5" and rejects junk.

    PYTHONIOENCODING=utf-8 python testing/test_pointing_graph_clicks.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_pgraph_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                                    # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt                # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent                    # noqa: E402
from PySide6.QtWidgets import QApplication                            # noqa: E402

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


def click(panel, x, y):
    mouse(panel, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton, x, y)
    mouse(panel, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton, x, y)


def wheel(panel, x, y, up=True):
    c = panel._canvas
    pos = QPointF(x, y)
    ev = QWheelEvent(pos, QPointF(c.mapToGlobal(pos.toPoint())), QPoint(0, 0),
                     QPoint(0, 120 if up else -120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    return panel.eventFilter(c, ev)


def dblclick(panel, x, y):
    mouse(panel, QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton, x, y)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    panel = is_t.PointingPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.resize(1000, 500)

    rng = np.random.default_rng(3)
    n = 60
    cx = rng.normal(5.0, 3.0, n)
    cy = rng.normal(-2.0, 2.0, n)
    # As the Viewer hands them in: measured from the first shot.
    cx -= cx[0]
    cy -= cy[0]
    ts =(1_750_000_000_000_000_000 + np.arange(n, dtype=np.int64) * 1_000_000_000)
    panel.plot(cx, cy, n, ts_ns=ts.astype(np.float64), ts_ns_int=ts,
               img_w=640, img_h=480)
    panel.show()
    app.processEvents()
    panel._draw()
    app.processEvents()

    got = []
    panel.point_clicked.connect(got.append)
    ax = panel._get_ax_main()

    k = 17
    qx, qy = panel._data_to_qt(ax, float(panel._cx[k]), float(panel._cy[k]))
    click(panel, qx, qy)
    check("a click on a point emits that point", got == [k], f"{got}")
    check("the clicked point gets the ring", panel._cur_idx == k
          and panel._cur_ring.isVisible(), f"idx={panel._cur_idx}")

    # Empty space: far corner of the scatter axes.
    got.clear()
    bb = ax.get_position()
    click(panel, bb.x0 * panel._canvas.width() + 2,
          (1 - bb.y1) * panel._canvas.height() + 2)
    check("a click on empty space emits nothing", got == [], f"{got}")

    # Replay hides later points; an ordinary set_current_ts must not.
    panel.set_current_ts(int(ts[5]))
    check("set_current_ts marks the matching point", panel._cur_idx == 5)
    check("…and hides nothing", panel._replay_ts is None)
    panel.set_current_ts(int(ts[5]) + 1)
    check("a frame that was not analysed marks nothing", panel._cur_idx is None)

    # Beam path.
    panel.toggle_path()
    app.processEvents()
    pax = panel._path_ax
    check("the beam path is drawn", pax is not None)
    got.clear()
    k = 40
    pqx, pqy = panel._data_to_qt(pax, float(panel._cx[k]), float(panel._cy[k]))
    click(panel, pqx, pqy)
    check("a click on the beam path emits that shot", got == [k], f"{got}")

    main_before = panel._get_ax_main().get_xlim()
    path_before = pax.get_xlim()
    wheel(panel, pqx, pqy, up=True)
    wheel(panel, pqx, pqy, up=True)
    path_after = panel._path_ax.get_xlim()
    check("the wheel over the path zooms the path",
          (path_after[1] - path_after[0]) < (path_before[1] - path_before[0]) * 0.8,
          f"{path_before} → {path_after}")
    check("…and leaves the cloud alone",
          np.allclose(panel._get_ax_main().get_xlim(), main_before))
    panel._draw()
    check("a redraw keeps the path zoom",
          np.allclose(panel._path_ax.get_xlim(), path_after),
          f"{panel._path_ax.get_xlim()}")
    dblclick(panel, pqx, pqy)
    check("double-click on the path shows the whole path again",
          panel._user_path_xlim is None
          and np.allclose(panel._path_ax.get_xlim(), path_before),
          f"{panel._path_ax.get_xlim()} vs {path_before}")

    # The zero is the median shot (05.10.2026).
    def from_median(keep):
        return cx - np.median(cx[keep]), cy - np.median(cy[keep])

    all_kept = np.ones(n, dtype=bool)
    ex, ey = from_median(all_kept)
    check("(0, 0) is the median of the shots", np.allclose(panel._cx, ex)
          and np.allclose(panel._cy, ey)
          and abs(np.median(panel._cx)) < 1e-12 and abs(np.median(panel._cy)) < 1e-12)
    check("axis label says 'from median'",
          "from median" in panel._get_ax_main().get_xlabel())

    # Units.
    wheel(panel, *panel._data_to_qt(panel._get_ax_main(), 0.0, 0.0), up=True)
    px_view = (panel._user_xlim, panel._user_ylim)
    f = is_t.POINTING_URAD_PER_PX
    panel.set_unit("urad")
    check("µrad: same points × 0.227", np.allclose(panel._cx, ex * f))
    check("µrad: Y turned up", np.allclose(panel._cy, -ey * f))
    check("µrad: axis label says µrad",
          "µrad" in panel._get_ax_main().get_xlabel())
    xl, yl = panel._get_ax_main().get_xlim(), panel._get_ax_main().get_ylim()
    check("µrad: the zoom is carried over",
          np.allclose(xl, (px_view[0][0] * f, px_view[0][1] * f))
          and np.allclose(yl, (-px_view[1][0] * f, -px_view[1][1] * f)),
          f"{xl} {yl}")
    check("µrad: Y axis grows upwards", yl[1] > yl[0], f"{yl}")

    # Optics changed (Pixel / M / f): the µrad points and the zoom scale with it.
    f2 = is_t.pointing_urad_per_px(5.5, 1 / 50, 250.0)
    panel.set_optics(f2)
    check("new optics: points × the new factor", np.allclose(panel._cx, ex * f2))
    check("new optics: the zoom stays on the same points",
          np.allclose(panel._get_ax_main().get_xlim(),
                      (px_view[0][0] * f2, px_view[0][1] * f2)),
          f"{panel._get_ax_main().get_xlim()}")
    panel.set_optics(f)

    # mm = px × pixel size, Y up; µrad → mm → px keeps the zoom on the same points.
    g = is_t.pointing_mm_per_px(4.5)
    panel.set_unit("mm")
    check("mm: same points × 0.0045", np.allclose(panel._cx, ex * g))
    check("mm: Y turned up", np.allclose(panel._cy, -ey * g))
    check("mm: axis label says mm", "(mm from" in panel._get_ax_main().get_xlabel())
    check("mm: unit text and 4 decimals",
          panel.unit_text() == "mm" and panel._sigma_decimals() == 4)
    xl, yl = panel._get_ax_main().get_xlim(), panel._get_ax_main().get_ylim()
    check("mm: the zoom is carried over from µrad",
          np.allclose(xl, (px_view[0][0] * g, px_view[0][1] * g))
          and np.allclose(yl, (-px_view[1][0] * g, -px_view[1][1] * g)),
          f"{xl} {yl}")
    check("mm: σX = σ px × pixel / 1000",
          np.isclose(np.std(panel._cx), np.std(ex) * g))
    panel.set_optics(f, is_t.pointing_mm_per_px(5.5))
    check("mm: a new pixel size rescales the points",
          np.allclose(panel._cx, ex * 0.0055))
    panel.set_optics(f, g)

    panel.set_unit("px")
    check("back to px: the original view",
          np.allclose(panel._get_ax_main().get_xlim(), px_view[0])
          and np.allclose(panel._get_ax_main().get_ylim(), px_view[1]))
    check("back to px: the original points", np.allclose(panel._cx, ex))

    # The zero follows the median of the points still kept.
    sig0 = float(np.std(panel._cx[panel._mask]))
    view0 = panel._user_xlim
    old0 = np.median(cx)
    panel._delete_indices(np.array([0, 1, 2, 3, 4]))
    keep = all_kept.copy(); keep[:5] = False
    ex2, ey2 = from_median(keep)
    check("shots deleted: (0, 0) is the median of the rest",
          np.allclose(panel._cx, ex2) and np.allclose(panel._cy, ey2))
    shift = np.median(cx[keep]) - old0
    check("…the zoom moved with the zero",
          view0 is None or np.allclose(panel._user_xlim,
                                       (view0[0] - shift, view0[1] - shift)))
    check("…σ of the rest", np.isclose(np.std(panel._cx[panel._mask]), np.std(cx[keep]))
          and np.isclose(sig0, np.std(cx)))
    panel.undo_last_delete()
    check("undo: the median of all again", np.allclose(panel._cx, ex))
    panel._delete_indices(np.array([0, 1, 2, 3, 4]))
    st2 = panel.export_state()
    panel.plot(cx, cy, n, ts_ns=ts.astype(np.float64), ts_ns_int=ts,
               img_w=640, img_h=480, state=st2)
    check("back to a camera with shots deleted: zero = median of the rest",
          np.allclose(panel._cx, ex2))
    panel.restore_all_points()
    check("restore all: the median of all again", np.allclose(panel._cx, ex))

    # The other views draw and give the mouse to matplotlib.
    for view, n_axes in (("time", 3), ("3d", 2), ("xy_time", 3)):
        panel.set_view(view)
        app.processEvents()
        axes = panel._fig.get_axes()
        check(f"view {view}: drawn", len(axes) >= n_axes, f"{len(axes)} axes")
    check("the plain X–Y view is gone", "xy" not in panel.POINTING_VIEWS)
    panel.set_view("3d")
    check("3D: a click is left to matplotlib (to turn it)",
          mouse(panel, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
                500, 250) is False)
    check("3D: delete mode cannot be armed", panel.set_select_mode(True) is False)
    panel.set_view("xy")          # an old saved "xy" opens as "X–Y by time"
    check("a saved 'xy' opens as X–Y by time", panel._view == "xy_time")
    check("X–Y again: delete mode can be armed", panel.set_select_mode(True) is True)
    panel.set_select_mode(False)

    # The magnification as typed.
    for txt, want in (("1/66", 1 / 66), ("0,5", 0.5), (" 0.01515 ", 0.01515),
                      ("abc", None), ("1/0", None), ("-2", None), ("", None)):
        got_m = is_t.pointing_parse_m(txt)
        check(f"M typed as {txt!r}",
              (got_m is None and want is None)
              or (got_m is not None and want is not None and abs(got_m - want) < 1e-12),
              f"{got_m}")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f_ in FAILURES:
        print(f"  - {f_}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
