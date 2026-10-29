"""PCW3_NF wears its permanent reference square in the Slider.

The camera software draws a fixed reference square on the live screen, but that square
is not written into the archived PNG — so the Slider, which only ever sees the archive,
showed a bare picture. It is re-drawn here from a saved position.

What this checks, by RENDERING the real ImageView (not re-implementing the maths):

* the square lands on the measured fractions, within 2 px of where it belongs;
* it follows a zoom, i.e. it stays on the same sensor pixels rather than on the
  same place in the tile;
* turning it off leaves the picture clean;
* a camera that has no reference square gets none, and the diode grid no longer
  appears on cameras that are not diodes.

Rendered on the real windows platform (offscreen has no fonts and lies about sizes);
no share and no archiver are touched, and the settings file is redirected to a temp
folder so the user's own saved position is never written.
"""
import os
import sys
import tempfile
from pathlib import Path

# MUST precede any Qt import — render for real, the way the house rule asks.
os.environ["QT_QPA_PLATFORM"] = "windows"

import bench_common as B          # noqa: E402  (loads is_t.py as `image_slider`)

FAILURES: "list[str]" = []

PCW3 = "C03-081-PCW3NF-_-IMG"
OTHER = "C03-040-PTM11WNF-_-IMG"
DIODE = "C03-023-PD3M1DF-_-IMG"

VIEW_W, VIEW_H = 620, 640
OUT = Path(__file__).resolve().parent / "ref_rect_overlay.png"


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def dark_frame(side: int = 1024):
    """A 1024x1024 near-black frame, the size PCW3_NF really writes."""
    from PySide6.QtGui import QPixmap, QColor
    pm = QPixmap(side, side)
    pm.fill(QColor(8, 0, 0))
    return pm


def white_bbox(img, area):
    """Bounding box of the bright pixels inside `area`, as (l, t, r, b) or None.

    Only the picture itself is searched: the widget keeps a light margin around the
    frame, which is not part of any overlay."""
    left = top = 10 ** 9
    right = bottom = -1
    for y in range(area.top() + 1, area.bottom()):
        for x in range(area.left() + 1, area.right()):
            c = img.pixelColor(x, y)
            if c.red() > 180 and c.green() > 180 and c.blue() > 180:
                left = min(left, x); right = max(right, x)
                top = min(top, y);   bottom = max(bottom, y)
    if right < 0:
        return None
    return left, top, right, bottom


def quiet(view):
    """Shown without ever taking the keyboard focus from the user."""
    from PySide6.QtCore import Qt
    view.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    view.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    return view


def render(view):
    view.repaint()
    return view.grab().toImage()


def expected_edges(m, view, cfg, zoom=None):
    """Where the square must land, computed the way paintEvent does."""
    ir = view._img_rect()
    ln, tn, rn, bn = cfg.left, cfg.top, cfg.right, cfg.bottom
    if zoom is not None:
        zl, zt, zr, zb = zoom
        ln = (ln - zl) / (zr - zl); rn = (rn - zl) / (zr - zl)
        tn = (tn - zt) / (zb - zt); bn = (bn - zt) / (zb - zt)
    x = ir.left() + int(ln * ir.width())
    y = ir.top() + int(tn * ir.height())
    w = max(1, int((rn - ln) * ir.width()))
    h = max(1, int((bn - tn) * ir.height()))
    return x, y, x + w, y + h


def main():
    m = B.load_slider()

    # Never touch the user's own saved position.
    tmp = Path(tempfile.mkdtemp(prefix="ref_rect_test_"))
    m._CAM_REF_RECT_PATH = tmp / "cam_ref_rects.json"

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    print("\n-- which cameras have a reference square --")
    check("PCW3_NF folder name recognised", m._ref_rect_key(PCW3) == "PCW3_NF",
          f"got {m._ref_rect_key(PCW3)!r}")
    check("PCW3_NF display name recognised", m._ref_rect_key("C03-081_PCW3_NF") == "PCW3_NF",
          f"got {m._ref_rect_key('C03-081_PCW3_NF')!r}")
    check("PCW3_DF is not PCW3_NF", m._ref_rect_key("C03-084-PCW3DF-_-IMG") != "PCW3_NF",
          f"got {m._ref_rect_key('C03-084-PCW3DF-_-IMG')!r}")

    frames = m.get_ref_frames(PCW3)
    cfg = frames[0] if frames else None
    check("PCW3_NF has exactly its measured square", len(frames) == 1, f"{len(frames)} frames")
    check("on by default", cfg is not None and cfg.show is True)
    check("named 'Measured by hand'", cfg is not None and cfg.name == "Measured by hand"
          and cfg.source == "hand")
    check("other cameras start with none",
          m.get_ref_frames(OTHER) == [] and m.get_ref_frames(DIODE) == [])

    print("\n-- the square lands where it was measured --")
    view = quiet(m.ImageView())
    view.resize(VIEW_W, VIEW_H)
    view.pdxm1_cam_name = PCW3
    view.ref_rect_cfg = frames
    view.set_pixmap(dark_frame())
    view.show()
    app.processEvents()

    img = render(view)
    img.save(str(OUT))
    box = white_bbox(img, view._img_rect())
    check("square is drawn", box is not None)
    if box is not None:
        exp = expected_edges(m, view, cfg)
        off = [abs(a - b) for a, b in zip(box, exp)]
        check("all four edges within 2 px", max(off) <= 2,
              f"drawn={box} expected={exp} off={off}")
        # An independent sanity check against the numbers read off the screenshot,
        # so a wrong default cannot pass just because paintEvent agrees with itself.
        ir = view._img_rect()
        fr = ((box[0] - ir.left()) / ir.width(), (box[1] - ir.top()) / ir.height(),
              (box[2] - ir.left()) / ir.width(), (box[3] - ir.top()) / ir.height())
        want = (0.336, 0.162, 0.848, 0.668)
        check("edges match the measured fractions", max(abs(a - b) for a, b in zip(fr, want)) < 0.01,
              "drawn=" + ", ".join(f"{v:.3f}" for v in fr))

    print("\n-- the picture paints with a name bar and no energy text --")
    # This is what made the square show up and vanish again: the camera name strip
    # blew up on an unbound QFontMetrics whenever there was no energy text, and the
    # drawing stopped right there — before the square, which is painted last. The
    # painter was then left open on the widget, which is what killed the window on
    # the next move or resize. Painting is driven directly here so a failure is a
    # test failure and not just a line in the terminal.
    from PySide6.QtGui import QPainter, QPixmap
    for label, energy in (("no energy text", ""), ("with energy text", "E = 1.234 J")):
        view.cam_label_text = "C03-081_PCW3_NF"
        view.cam_ts_text = "09:41:02.113"
        view.energy_text = energy
        target = QPixmap(VIEW_W, VIEW_H)
        target.fill()
        painter = QPainter(target)
        try:
            view._paint_body(painter, None)
            check(f"paints cleanly ({label})", True)
        except Exception as exc:
            check(f"paints cleanly ({label})", False, f"{type(exc).__name__}: {exc}")
        finally:
            if painter.isActive():
                painter.end()
    view.cam_label_text = ""
    view.cam_ts_text = ""
    view.energy_text = ""
    app.processEvents()
    check("square still drawn afterwards",
          white_bbox(render(view), view._img_rect()) is not None)

    print("\n-- it follows a zoom --")
    zoom = (0.25, 0.10, 0.95, 0.80)
    view.set_zoom(zoom)
    app.processEvents()
    box_z = white_bbox(render(view), view._img_rect())
    check("square still drawn when zoomed", box_z is not None)
    if box_z is not None:
        exp_z = expected_edges(m, view, cfg, zoom=zoom)
        off = [abs(a - b) for a, b in zip(box_z, exp_z)]
        check("zoomed edges within 2 px", max(off) <= 2,
              f"drawn={box_z} expected={exp_z} off={off}")
        check("zoom actually moved it", box_z != box)
    view.set_zoom(None)
    app.processEvents()

    print("\n-- switching it off --")
    off_cfg = m.CamRefRectConfig(show=False)
    view.ref_rect_cfg = [off_cfg]
    app.processEvents()
    check("nothing drawn when off", white_bbox(render(view), view._img_rect()) is None)

    print("\n-- two frames, each with its own switch --")
    second = m.CamRefRectConfig(left=0.10, top=0.10, right=0.20, bottom=0.20,
                                id="x2", name="Second", source="window")
    view.ref_rect_cfg = [off_cfg, second]
    app.processEvents()
    box2 = white_bbox(render(view), view._img_rect())
    exp2 = expected_edges(m, view, second)
    check("only the shown one is drawn", box2 is not None
          and max(abs(a - b) for a, b in zip(box2, exp2)) <= 2, f"drawn={box2} expected={exp2}")

    print("\n-- saving and reloading a nudged position --")
    nudged = m.CamRefRectConfig(left=0.30, top=0.20, right=0.80, bottom=0.70, show=False)
    m.save_ref_frames(PCW3, [nudged])
    back = m.get_ref_frames("C03-081_PCW3_NF")   # the other spelling reads it too
    back = back[0] if back else None
    check("nudged position comes back", back is not None and abs(back.left - 0.30) < 1e-9
          and abs(back.bottom - 0.70) < 1e-9)
    check("the off switch is remembered", back is not None and back.show is False)
    m.save_ref_frame_show(PCW3, "hand-1", True)
    check("one switch saved on its own",
          m.get_ref_frames(PCW3)[0].show is True and abs(m.get_ref_frames(PCW3)[0].left - 0.30) < 1e-9)
    m._CAM_REF_RECT_PATH.unlink(missing_ok=True)

    print("\n-- the square saved by the previous version is kept --")
    import json as _json
    old = {"PCW3_NF": {"left": 0.31, "top": 0.17, "right": 0.85, "bottom": 0.66,
                       "color": "#00ff00", "width": 2, "alpha": 180, "show": False}}
    m._CAM_REF_RECT_PATH.write_text(_json.dumps(old), encoding="utf-8")
    fr = m.get_ref_frames(PCW3)
    check("old entry becomes one frame", len(fr) == 1, f"{len(fr)} frames")
    if fr:
        f0 = fr[0]
        check("its position, style and switch are unchanged",
              (f0.left, f0.top, f0.right, f0.bottom, f0.color, f0.width, f0.alpha, f0.show)
              == (0.31, 0.17, 0.85, 0.66, "#00ff00", 2, 180, False))
        check("it is the hand-made one", f0.name == "Measured by hand" and f0.source == "hand")
    # Adding a frame later must leave it as it is.
    m.save_ref_frames(PCW3, fr + [second])
    fr2 = m.get_ref_frames(PCW3)
    check("adding a frame keeps the old one", len(fr2) == 2 and fr2[0].left == 0.31
          and fr2[0].color == "#00ff00" and fr2[1].name == "Second")
    m.save_ref_frames(OTHER, [second])
    check("frames of another camera are its own", len(m.get_ref_frames(OTHER)) == 1
          and len(m.get_ref_frames(PCW3)) == 2)
    m._CAM_REF_RECT_PATH.unlink(missing_ok=True)

    print("\n-- other cameras stay clean --")
    v2 = quiet(m.ImageView())
    v2.resize(VIEW_W, VIEW_H)
    v2.pdxm1_cam_name = OTHER
    v2.ref_rect_cfg = m.get_ref_frames(OTHER)
    v2.set_pixmap(dark_frame(600))
    v2.show()
    app.processEvents()
    check("no square on another camera", white_bbox(render(v2), v2._img_rect()) is None)

    # A stale saved flag must not resurrect the diode grid on a non-diode camera.
    v2.show_pdxm1_grid = True
    app.processEvents()
    check("no diode grid on a non-diode camera", white_bbox(render(v2), v2._img_rect()) is None)
    check("_is_diode_cam agrees", m._is_diode_cam(DIODE) and not m._is_diode_cam(OTHER)
          and not m._is_diode_cam(PCW3))

    view.close(); v2.close()
    print(f"\nrendered: {OUT}")
    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES)))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
