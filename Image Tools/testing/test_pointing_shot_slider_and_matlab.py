"""Pointing graph — shot slider, one ring, rainbow time colours, Kuba's method
(06.10.2026).

What the user asked for, and what is checked here (in process, offscreen, events
handed straight to the panel's own event filter; no window, no desktop input):

  * the plain X–Y view is gone; every time colour is the rainbow ("turbo");
  * dragging a time cursor leaves ONE marker: the red ring (no lime dot);
  * "X, Y over shots" has a horizontal shot slider between the X and the Y graph:
    a drag picks the shot under it, loads its frame, puts a line through both
    graphs and a ring on the X and the Y point; the viewer stepping moves it too;
    deleted shots are not on it;
  * Kuba's method (pointing_stability_from_batch_of_files.m) gives the same
    centroid as the MATLAB arithmetic done by hand on a uint8 frame, and its
    graph measures from the MEAN shot. (On the real 3 Sep 2026 C310 frames it
    matched his 1225 centroids to 2e-5 px: σX 1.938, σY 2.209 µrad both ways.)
  * with the graph open the camera tiles are laid out for the narrower canvas
    (the layout cache used to hand back the wide arrangement), so the labels sit
    on the frames.

    PYTHONIOENCODING=utf-8 python testing/test_pointing_shot_slider_and_matlab.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_pslider_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                                    # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt                        # noqa: E402
from PySide6.QtGui import QMouseEvent                                 # noqa: E402
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


def centre_of(lbl):
    return lbl.x() + lbl.width() / 2.0, lbl.y() + lbl.height() / 2.0


def matlab_by_hand(img_u8, thr):
    """The MATLAB lines, with uint8 arithmetic: subtraction saturates at 0."""
    bg = int(img_u8[:, 0].max())
    img = np.where(img_u8.astype(np.int32) - bg < 0, 0,
                   img_u8.astype(np.int32) - bg).astype(np.uint8)
    img[~(img > thr)] = 0
    X = np.arange(1, img.shape[1] + 1)               # 1-based like MATLAB
    Y = np.arange(1, img.shape[0] + 1)
    irr = img.astype(np.float64).sum()
    cen_x = (img.astype(np.float64).sum(axis=0) @ X) / irr
    cen_y = (img.astype(np.float64).sum(axis=1) @ Y) / irr
    return cen_x - 1.0, cen_y - 1.0                  # → 0-based


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    # ── Kuba's centroid ──────────────────────────────────────────────────────
    rng = np.random.default_rng(11)
    h, w = 240, 320
    yy, xx = np.mgrid[0:h, 0:w]
    spot = 230.0 * np.exp(-(((xx - 201.3) / 14.0) ** 2 + ((yy - 97.6) / 9.0) ** 2) / 2)
    frame = np.clip(spot + 12 + rng.normal(0, 4, (h, w)), 0, 255).astype(np.uint8)
    want = matlab_by_hand(frame, 100)
    got = is_t.pointing_centroid_matlab(frame, 100)
    check("Kuba's method = the MATLAB arithmetic by hand",
          got is not None and abs(got[0] - want[0]) < 1e-9 and abs(got[1] - want[1]) < 1e-9,
          f"{got[:2] if got else None} vs {want}")
    check("…near the true spot centre", got is not None
          and abs(got[0] - 201.3) < 0.5 and abs(got[1] - 97.6) < 0.5)
    check("…nothing above the threshold gives no point",
          is_t.pointing_centroid_matlab(np.full((20, 20), 50, np.uint8), 100) is None)
    # Through the worker, from a PNG on disk.
    from PIL import Image
    p = _TMP / "frame.png"
    Image.fromarray(frame).save(p)

    class _It:
        path = p
        ts_ns = 123
    r = is_t.PointingAnalysisTask._process_one(_It(), 100, 1.0, "matlab")
    check("the worker returns Kuba's centroid from the sensor centre",
          r is not None and abs(r[1] - (want[0] - (w - 1) / 2)) < 1e-9
          and abs(r[2] - (want[1] - (h - 1) / 2)) < 1e-9, f"{r}")
    r2 = is_t.PointingAnalysisTask._process_one(_It(), 90, 1.0, "peak")
    check("Peak core still works on the same frame",
          r2 is not None and abs(r2[1] + (w - 1) / 2 - 201.3) < 0.5, f"{r2}")

    # ── Panel ────────────────────────────────────────────────────────────────
    panel = is_t.PointingPanel()
    panel.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    panel.resize(1000, 560)
    n = 200
    cx = rng.normal(0, 3, n)
    cy = rng.normal(0, 2, n)
    t0 = 1_759_650_000 * 1_000_000_000
    ts = t0 + np.arange(n, dtype=np.int64) * 10 * 10**9
    panel.plot(cx, cy, n, ts_ns=ts.astype(np.float64), ts_ns_int=ts,
               img_w=640, img_h=480, ref_kind="mean")
    panel.show()
    app.processEvents()
    check("Kuba's run: zero = the mean shot",
          abs(float(np.mean(panel._cx))) < 1e-9 and "from mean" in
          panel._get_ax_main().get_xlabel())
    check("plain X–Y view gone, opens as X–Y by time", panel._view == "xy_time")
    coll = panel._get_ax_main().collections[0]
    check("the X–Y cloud is coloured by the rainbow",
          coll.get_cmap().name == "turbo", coll.get_cmap().name)
    check("the time bar is the rainbow too", panel._cbar_ax is not None)

    got_idx = []
    panel.point_clicked.connect(got_idx.append)

    # Beam path: the time cursor drag leaves one marker — the ring.
    panel.toggle_path()
    app.processEvents()
    x0, yt, x1, yb = panel._cbar_rect_px()
    xm = (x0 + x1) / 2
    mouse(panel, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton, xm, yb - 2)
    mouse(panel, QEvent.Type.MouseMove, Qt.MouseButton.LeftButton, xm,
          yt + 0.5 * (yb - yt))
    k = panel._tc_k
    check("drag on the bar: the ring jumps to that shot at once",
          panel._cur_idx == int(panel._tc_idx[k]) and panel._cur_ring.isVisible())
    qx, qy = panel._data_to_qt(panel._path_ax, float(panel._cx[k]), float(panel._cy[k]))
    cxr, cyr = centre_of(panel._cur_ring2)
    check("…a ring on the beam path too", panel._cur_ring2.isVisible()
          and abs(cxr - qx) <= 1.5 and abs(cyr - qy) <= 1.5)
    check("…and no lime dot", not hasattr(panel, "_path_cursor"))
    mouse(panel, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton, xm,
          yt + 0.5 * (yb - yt))
    panel.toggle_path()

    # ── X, Y over shots: the shot slider ─────────────────────────────────────
    panel.set_current_idx(None)
    panel.set_view("time")
    app.processEvents()
    check("over shots: a slider strip between the graphs", panel._hs_ax is not None)
    ax_x, ax_y = panel._hs_ax_x, panel._hs_ax_y
    sb = panel._hs_ax.get_position()
    check("…below the X graph and above the Y graph",
          ax_y.get_position().y1 < sb.y0 and sb.y1 < ax_x.get_position().y0)
    check("…the knob is there with no shot picked (on the last shot)",
          panel._hs_knob.isVisible() and not panel._cur_ring.isVisible())
    hx0, hyt, hx1, hyb = panel._hs_rect_px(panel._hs_ax)
    ymid = (hyt + hyb) / 2
    got_idx.clear()
    took = mouse(panel, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
                 hx0 + 5, ymid)
    check("a press on the strip is the slider's", took is True)
    want_shot = 60
    qx = panel._qt_pos_of(panel._hs_ax, want_shot, 0.5)[0]
    mouse(panel, QEvent.Type.MouseMove, Qt.MouseButton.LeftButton, qx, ymid)
    mouse(panel, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton, qx, ymid)
    check("the drag picks shot 60 and loads its frame",
          panel._cur_idx == want_shot - 1 and got_idx and got_idx[-1] == want_shot - 1,
          f"cur={panel._cur_idx} sent={got_idx[-3:]}")
    kx, _ky = centre_of(panel._hs_knob)
    check("…the knob sits on it", abs(kx - qx) <= 1.5, f"{kx} vs {qx}")
    for ln, gax, name in zip(panel._hs_lines, (ax_x, ax_y), ("X", "Y")):
        _x0, gyt, _x1, gyb = panel._hs_rect_px(gax)
        check(f"…a line through the {name} graph", ln.isVisible()
              and abs(ln.x() - qx) <= 1.0 and abs(ln.y() - gyt) <= 1.0
              and abs(ln.y() + ln.height() - gyb) <= 1.5)
    px_, py_ = panel._data_to_qt(ax_x, want_shot, float(panel._cx[want_shot - 1]))
    check("…a ring on the X point", panel._cur_ring.isVisible()
          and max(abs(a - b) for a, b in zip(centre_of(panel._cur_ring), (px_, py_))) <= 1.5)
    px_, py_ = panel._data_to_qt(ax_y, want_shot, float(panel._cy[want_shot - 1]))
    check("…a ring on the Y point", panel._cur_ring2.isVisible()
          and max(abs(a - b) for a, b in zip(centre_of(panel._cur_ring2), (px_, py_))) <= 1.5)
    check("…the readout names the time and the shot",
          panel._hs_lbl.isVisible() and "shot 60" in panel._hs_lbl.text()
          and is_t.fmt_prague_full_from_ns(int(ts[59]))[11:19] in panel._hs_lbl.text(),
          panel._hs_lbl.text())
    lbl = panel._hs_lbl
    check("…and stays inside the canvas",
          lbl.x() >= 0 and lbl.x() + lbl.width() <= panel._canvas.width())

    # The viewer stepping moves the slider.
    panel.set_current_ts(int(ts[150]))
    q150 = panel._qt_pos_of(panel._hs_ax, 151, 0.5)[0]
    check("viewer on shot 151 → the knob follows",
          abs(centre_of(panel._hs_knob)[0] - q150) <= 1.5)

    # Deleted shots are not on the strip.
    panel.set_view("xy_time")
    panel._delete_indices(np.arange(0, 20))
    panel.set_view("time")
    app.processEvents()
    check("deleted shots are left off the slider",
          len(panel._hs_idx) == n - 20 and int(panel._hs_idx[0]) == 20)
    hx0, hyt, hx1, hyb = panel._hs_rect_px(panel._hs_ax)
    got_idx.clear()
    mouse(panel, QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton, hx0 + 1,
          (hyt + hyb) / 2)
    mouse(panel, QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton, hx0 + 1,
          (hyt + hyb) / 2)
    check("…its left end is the first shot kept", got_idx and got_idx[-1] == 20,
          f"{got_idx}")

    # Camera tiles beside the open graph: a canvas half as wide must get its own
    # arrangement, not the wide one from the layout cache (651 // 8 == 1301 // 16).
    wide = is_t.compute_camera_layout([1.06, 1.06], 1301, 765, 39, [1.0, 1.0])
    narrow = is_t.compute_camera_layout([1.06, 1.06], 651, 765, 39, [1.0, 1.0])
    check("wide canvas: two cameras side by side", wide[0].w < 0.6 and wide[0].h > 0.9)
    check("half-width canvas (graph open): one above the other, labels on the frames",
          narrow[0].w > 0.9 and narrow[0].h < 0.6,
          f"{[(e.x, e.y, e.w, e.h) for e in narrow]}")

    # Leaving the view hides the slider.
    panel.set_view("3d")
    app.processEvents()
    check("other views: no shot slider",
          not panel._hs_knob.isVisible() and not panel._hs_lbl.isVisible()
          and not any(ln.isVisible() for ln in panel._hs_lines))

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f_ in FAILURES:
        print(f"  - {f_}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
