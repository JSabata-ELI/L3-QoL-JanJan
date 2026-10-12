"""Image Slider, 05.10.2026 — three of the four changes asked for on 02.10.2026:

1. Units: `mm^2` typed, `mm²` shown (pv_units_for); the typed form stays editable
   (pv_units_typed).
2. INFO counter: shots across cameras (ShotGrouper / shot_tolerance_ns) — frames of
   different cameras a few ms apart are one shot, two frames of one camera never are,
   and feeding a live tail later gives the same count as one full pass.
3. Marks on a zoomed camera stay on the same sensor pixels (zoom_full_rect /
   ImageView._mark_rect), and come back exactly when the zoom is reset.

(The fourth, pointing, is in test_pointing_centroid.py.)

    PYTHONIOENCODING=utf-8 python testing/test_marks_shots_units.py

Offscreen: no window is shown, nothing takes the keyboard.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = Path(tempfile.mkdtemp(prefix="eli_is_msu_"))
os.environ["APPDATA"] = str(_TMP)

from PySide6.QtWidgets import QApplication                            # noqa: E402
from PySide6.QtGui import QPixmap, QColor                             # noqa: E402
from PySide6.QtCore import QPointF                                    # noqa: E402

import is_t                                                           # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def units():
    print("units")
    check("mm^2 → mm²", is_t.unit_text("mm^2") == "mm²", is_t.unit_text("mm^2"))
    check("mJ/cm^2 → mJ/cm²", is_t.unit_text("mJ/cm^2") == "mJ/cm²")
    check("cm^-1 → cm⁻¹", is_t.unit_text("cm^-1") == "cm⁻¹")
    check("fs^2 and fs³ side by side", is_t.unit_text("fs^2 fs^3") == "fs² fs³")
    check("a lone caret is left alone", is_t.unit_text("a^b") == "a^b")
    check("already lifted stays", is_t.unit_text("mm²") == "mm²")
    name = "TEST_PV_AREA"
    is_t.PV_CUSTOM_UNITS[name] = "mm^2"
    try:
        check("pv_units_for shows the superscript",
              is_t.pv_units_for(name) == "mm²", is_t.pv_units_for(name))
        check("pv_units_typed keeps what was typed",
              is_t.pv_units_typed(name) == "mm^2", is_t.pv_units_typed(name))
    finally:
        is_t.PV_CUSTOM_UNITS.pop(name, None)


def shots():
    print("shots across cameras")
    MS = 1_000_000
    # 3 cameras at 1 Hz, stamping each shot up to 7 ms apart; camera 3 also has a
    # frame of its own between the shots (it runs at 2 Hz).
    cams = [[], [], []]
    for k in range(20):
        t = 1_000_000_000_000 + k * 1000 * MS
        cams[0].append(t)
        cams[1].append(t + 3 * MS)
        cams[2].append(t + 7 * MS)
        cams[2].append(t + 500 * MS)
    cams = [sorted(c) for c in cams]
    tol = is_t.shot_tolerance_ns(cams)
    # Camera 3's gaps are 493 / 507 ms (median 493): half of that.
    check("tolerance = half the fastest camera's usual gap (~246 ms)",
          tol == 493 * MS // 2, f"{tol / MS:.1f} ms")
    g = is_t.ShotGrouper(tol)
    frames = sorted((t, ci) for ci, c in enumerate(cams) for t in c)
    g.feed(frames)
    check("80 images make 40 shots (20 shared + 20 of camera 3 alone)",
          len(frames) == 80 and g.n == 40, f"{len(frames)} images, {g.n} shots")
    check("the three stamps of one shot are one shot",
          g.shot_of_ts[cams[0][5]] == g.shot_of_ts[cams[1][5]]
          == g.shot_of_ts[cams[2][10]])
    # Live: the same frames in two batches.
    g2 = is_t.ShotGrouper(tol)
    g2.feed(frames[:37])
    g2.feed(frames[37:])
    check("fed in two batches = fed at once", g2.n == g.n and g2.shot_of_ts == g.shot_of_ts)
    # Two frames of ONE camera never make one shot, however close.
    g3 = is_t.ShotGrouper(500 * MS)
    g3.feed([(0, 0), (10 * MS, 0), (12 * MS, 1)])
    check("one camera twice = two shots", g3.n == 2, f"{g3.n}")
    # Identical stamps on every camera (the old exact-match case) still count once.
    same = [[k * 1000 * MS for k in range(10)] for _ in range(4)]
    g4 = is_t.ShotGrouper(is_t.shot_tolerance_ns(same))
    g4.feed(sorted((t, ci) for ci, c in enumerate(same) for t in c))
    check("4 cameras × 10 identical stamps = 10 shots", g4.n == 10, f"{g4.n}")
    check("one frame per camera → the 2 s cap",
          is_t.shot_tolerance_ns([[5], [7]]) == is_t.SHOT_TOL_MAX_NS)
    # 5 Oct 2026: one frame per 5 s, one shot's frames spread up to 0.68 s over the
    # cameras. 8 cameras × 100 shots must be 100 merged, not split.
    spread = [0, 120, 250, 330, 410, 520, 600, 680]
    slow = [[k * 5000 * MS + d * MS for k in range(100)] for d in spread]
    g5 = is_t.ShotGrouper(is_t.shot_tolerance_ns(slow))
    g5.feed(sorted((t, ci) for ci, c in enumerate(slow) for t in c))
    check("5 s cameras, 0.68 s spread: 800 images = 100 shots", g5.n == 100, f"{g5.n}")


def marks():
    print("marks under zoom")
    app = QApplication.instance() or QApplication([])
    iv = is_t.ImageView()
    iv.resize(800, 640)
    pm = QPixmap(1000, 800)
    pm.fill(QColor(30, 30, 30))
    iv.set_pixmap(pm)
    ir0 = iv._img_rect()
    check("no zoom: mark rect = image rect", iv._mark_rect() == ir0,
          f"{iv._mark_rect()} vs {ir0}")

    # A circle on sensor pixel (600, 300), radius 40 px.
    iv.add_mark("circle", (600 / 1000, 300 / 800, 40 / 1000, 40 / 800))
    geom0 = tuple(iv.marks["circle"][0])

    def sensor_of(screen_x, screen_y):
        """Screen point → sensor pixel through the crop actually shown."""
        ir = iv._img_rect()
        z = iv._zoom_norm or (0.0, 0.0, 1.0, 1.0)
        cx, cy = int(z[0] * 1000), int(z[1] * 800)
        cw = max(1, int((z[2] - z[0]) * 1000)); ch = max(1, int((z[3] - z[1]) * 800))
        return (cx + (screen_x - ir.left()) / ir.width() * cw,
                cy + (screen_y - ir.top()) / ir.height() * ch)

    iv.set_zoom((0.5, 0.25, 0.75, 0.5))           # 4× in, on the circle
    mr = iv._mark_rect()
    hnd = is_t.mark_handles(mr, "circle", iv.marks["circle"][0])
    c = hnd["move"]; e = hnd["e"]
    sx, sy = sensor_of(c.x(), c.y())
    check("zoomed 4×: circle centre still on sensor pixel (600, 300)",
          abs(sx - 600) < 1.0 and abs(sy - 300) < 1.0, f"({sx:.2f}, {sy:.2f})")
    ex, _ = sensor_of(e.x(), e.y())
    check("zoomed 4×: radius still 40 sensor px", abs(ex - 640) < 1.0, f"{ex - 600:.2f}")
    check("zoomed 4×: circle is 4× bigger on screen",
          abs((e.x() - c.x()) - 4 * 40 * ir0.width() / 1000) < 2.0,
          f"{e.x() - c.x():.1f} px")

    # A cross placed while zoomed lands on the sensor pixel under the cursor.
    ir = iv._img_rect()
    at = QPointF(ir.left() + ir.width() * 0.3, ir.top() + ir.height() * 0.6)
    want = sensor_of(at.x(), at.y())
    iv._place_cross_at(at, iv._mark_rect(), new=True)
    gx, gy = iv.marks["cross"][-1][:2]
    check("cross put down while zoomed sits on the pixel clicked",
          abs(gx * 1000 - want[0]) < 1.0 and abs(gy * 800 - want[1]) < 1.0,
          f"({gx * 1000:.1f}, {gy * 800:.1f}) vs ({want[0]:.1f}, {want[1]:.1f})")

    iv.reset_zoom()
    check("zoom reset: the circle is exactly as drawn",
          tuple(iv.marks["circle"][0]) == geom0)
    hnd = is_t.mark_handles(iv._mark_rect(), "circle", iv.marks["circle"][0])
    sx, sy = sensor_of(hnd["move"].x(), hnd["move"].y())
    check("zoom reset: circle centre on (600, 300)",
          abs(sx - 600) < 1.0 and abs(sy - 300) < 1.0, f"({sx:.2f}, {sy:.2f})")
    # "When I zoom in, the circle changes its proportions" (05.10.2026): a zoom box
    # of ANY shape — wide, tall — must scale the circle and the square by the same
    # factor in x and y, so a round circle stays round and a square stays square.
    iv.reset_zoom()
    iv.add_mark("square", (0.2, 0.2, 0.2 + 100 / 1000, 0.2 + 100 / 800))
    def shape_ratios():
        mr = iv._mark_rect()
        h_c = is_t.mark_handles(mr, "circle", iv.marks["circle"][0])
        h_s = is_t.mark_handles(mr, "square", iv.marks["square"][0])
        rc = (h_c["e"].x() - h_c["move"].x()) / (h_c["s"].y() - h_c["move"].y())
        rs = (h_s["se"].x() - h_s["nw"].x()) / (h_s["se"].y() - h_s["nw"].y())
        return rc, rs
    base_c, base_s = shape_ratios()
    for z in ((0.5, 0.25, 0.75, 0.5), (0.1, 0.3, 0.9, 0.4), (0.55, 0.05, 0.6, 0.95),
              (0.3, 0.2, 0.33, 0.23)):
        iv.set_zoom(z)
        rc, rs = shape_ratios()
        check(f"zoom {z}: circle keeps its shape, square stays square",
              abs(rc / base_c - 1) < 0.02 and abs(rs / base_s - 1) < 0.02,
              f"circle {base_c:.3f}→{rc:.3f}, square {base_s:.3f}→{rs:.3f}")

    # Painting through the clip must not fail on a zoomed view.
    iv.set_zoom((0.1, 0.1, 0.4, 0.4))
    img = iv.grab()
    check("a zoomed view with marks paints", not img.isNull())
    del app


def main() -> int:
    units()
    shots()
    marks()
    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
