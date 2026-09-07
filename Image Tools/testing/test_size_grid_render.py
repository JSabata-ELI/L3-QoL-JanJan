"""The sizes as they come out on screen, in the REAL camera grid.

The reported bug was read off the arrangement board: four cameras set to Largest,
Smallest, Medium and Small came out with Smallest the second biggest frame and
Medium among the two smallest. Numbers alone were what let that ship — the packer
was scored on "the smallest frame, per share asked for", which those tiles passed.
So this one builds the real windows in the real auto-layout container, measures the
PICTURE inside each one the way the eye reads it, and also saves a screenshot.

Checked:
  * every frame is in the order the sizes asked for — nothing bigger than a camera
    that asked for more;
  * the ratios themselves, within a tenth (Largest really is 16x Smallest);
  * the windows still cover the whole area with no gaps and no overlaps;
  * the same holds for the size labels drawn on the arrangement board, so the board
    and the grid tell the same story.

Rendering uses the real windows platform: offscreen has no fonts and would make the
label bar (which eats into the picture) the wrong height.

Not shipped: the builder keeps test_* out of the bundle.

Usage:

    python testing/test_size_grid_render.py
    python testing/test_size_grid_render.py --shot out.png

Exit code is 1 when a claim fails.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="eli_size_grid_")

import is_t as sl                                     # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout   # noqa: E402


FAILURES: list = []


def check(ok: bool, what: str, detail: str = ""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  - " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", help="save a screenshot of the grid here")
    args = ap.parse_args()

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; } "
                      "QLabel { background: transparent; }")

    # The reported set: one of each of four sizes, all square frames.
    cams = ["C03-100-AAA1FF-IMG", "C03-101-BBB1FF-IMG",
            "C03-102-CCC1FF-IMG", "C03-103-DDD1FF-IMG"]
    want = ["largest", "smallest", "medium", "small"]
    for c, k in zip(cams, want):
        sl.set_cam_size_class(c, k)

    host = QWidget()
    host.resize(1500, 820)
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    views = [sl.CameraView(i, c, host) for i, c in enumerate(cams)]
    for v in views:
        v.set_label_font_size(20)
    cont = sl._AutoLayoutContainer(views, parent=host)
    hl.addWidget(cont)
    host.show()
    for _ in range(10):
        app.processEvents()

    print("The sizes on screen, in the real grid")
    label_px = views[0].image_overhead_px()
    aspects = [sl._cam_aspect_hint(c) for c in cams]
    weights = [sl._cam_layout_weight(c) for c in cams]
    areas, boxes = [], []
    for i, v in enumerate(views):
        g = v.geometry()
        boxes.append(g)
        areas.append(sl._tile_image_area(g.width(), g.height(), aspects[i], label_px))

    told = " ".join(f"{sl._CAM_SIZE_LABEL[want[i]]}={areas[i]/1000:.0f}k"
                    for i in range(len(cams)))
    check(sl._size_order_kept(areas, weights),
          "no frame is bigger than one that asked for a bigger size", told)
    miss = sl._size_mismatch(areas, weights)
    check(miss <= 1.10, "the frames come out at the asked ratio",
          f"worst off by {100 * (miss - 1.0):.0f}%")
    ratio = areas[0] / max(1e-9, areas[1])
    check(13.0 <= ratio <= 19.0, "Largest really is sixteen times Smallest",
          f"{ratio:.1f}x")

    covered = sum(b.width() * b.height() for b in boxes)
    area = cont.width() * cont.height()
    check(abs(covered - area) <= 4 * (cont.width() + cont.height()),
          "the windows cover the whole camera area",
          f"{100.0 * covered / max(1, area):.1f}% of it")
    overlap = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            inter = boxes[i].intersected(boxes[j])
            overlap += max(0, inter.width()) * max(0, inter.height())
    check(overlap == 0, "no two windows overlap", f"{overlap} px^2")

    # And the board the user actually decides on must say the same thing.
    board = sl._LayoutCanvasWidget(cam_names=cams, aspects=aspects, label_px=label_px,
                                   canvas_px=(cont.width(), cont.height()),
                                   label_font_px=20)
    board.resize(900, int(900 * cont.height() / cont.width()))
    board.show()
    for _ in range(6):
        app.processEvents()
    b_areas = []
    for c in cams:
        j = board._cam_names.index(c)
        r = board._tile_rect(j)
        img = board._image_rect_in(r, aspects[board._cam_names.index(c)])
        b_areas.append(img.width() * img.height())
    check(sl._size_order_kept(b_areas, weights),
          "the board shows them in the same order as the grid",
          " ".join(f"{sl._CAM_SIZE_LABEL[want[i]]}={b_areas[i]/1000:.0f}k"
                   for i in range(len(cams))))

    if args.shot:
        p = Path(args.shot)
        host.grab().save(str(p.with_name(p.stem + "_grid" + p.suffix)))
        board.grab().save(str(p.with_name(p.stem + "_board" + p.suffix)))
        print(f"  screenshots written next to {p}")

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
