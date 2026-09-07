"""Auto-arrange: one arrangement per camera set, and it fills the canvas.

Two complaints, one cause. For a camera set with sizes set (two Small, three Medium,
one Large) pressing a SIZE button and pressing AUTO-ARRANGE produced two different
arrangements of the same cameras, and both left a lot of the canvas black.

The cause was the order the cameras were handed to the packer. A split arrangement can
only put two cameras side by side if they are NEIGHBOURS in that order, so the order
decides which arrangements can be built at all — and the search only ever tried the
order it was given plus three or four sortings of it. The two buttons handed in
different orders (the size button the remembered arrangement's order, Auto-arrange the
selected list's), so they searched different arrangements. Measured on that set at
1424x782: of the 720 possible orders, 328 build an arrangement filling 74 % of the
canvas and 392 stop at 65 % — and every order the app used was in the 65 % group.

What is pinned here:

  * **The canvas is filled.** The reported set reaches at least 72 % of the canvas,
    where it used to reach 65 %. Fill is measured as PICTURE area — what a frame really
    shows inside its tile, not the tile.
  * **The order handed in does not change the answer.** The same cameras in any order
    must cut the canvas into the same tiles and give every camera the same picture.
    This is the bug itself. Which camera sits in which of two equally good tiles does
    still follow the list handed in — that is a deliberate tie-break, so that the
    cameras read in the order they were picked.
  * **A size press and Auto-arrange agree**, driven through the real picker with a
    remembered hand-made arrangement seeded, which is what made them disagree.
  * **Nothing is drawn at random.** Two runs from cold give identical fractions.
  * **The sizes are still honoured** — no frame bigger than one that asked for more,
    and the asked ratios reached — because a wider search is worthless if it wins the
    canvas by ignoring what the user asked for.
  * **The arrangements are built once per camera set.** Resizing the window must not
    rebuild them; that is what pays for the wider search.

Usage:

    python testing/test_auto_arrange.py

Exit code is 1 when a claim fails, so it can gate a build.
"""

import itertools
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="eli_arrange_test_")
os.environ["APPDATA"] = _TMP

import is_t as sl                                     # noqa: E402

# The set that was reported, with the sizes it had.
CAMS = ["C03-041-PASF1NF-_-IMG", "C03-042-PASF2NF-_-IMG", "C03-051-WRT2DPNF-_-IMG",
        "C03-040-PFM13NF-_-IMG", "C03-039-PAM10NF-_-IMG", "C03-035-PTM11wNF-_-IMG"]
SIZES = ["small", "small", "medium", "medium", "medium", "large"]
ASPECTS = [1.311, 1.311, 1.0, 1.294, 1.0, 0.95]
W, H, L = 1424.0, 782.0, 28.0

FAILURES: list = []


def check(ok: bool, what: str, detail: str = ""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  - " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)


def weights():
    return [sl._CAM_SIZE_WEIGHT[k] for k in SIZES]


def arrange(order=None, w=W, h=H, aspects=None, wts=None):
    """Fractions per camera, in CAMERA order whatever order the packer was handed."""
    a = list(aspects if aspects is not None else ASPECTS)
    wt = list(wts if wts is not None else weights())
    o = list(order) if order is not None else list(range(len(a)))
    ent = sl.compute_camera_layout([a[i] for i in o], w, h, L, [wt[i] for i in o])
    out = [None] * len(o)
    for e, i in zip(ent, o):
        out[i] = (e.x, e.y, e.w, e.h)
    return out


def picture_areas(tiles, w=W, h=H, aspects=None):
    a = list(aspects if aspects is not None else ASPECTS)
    return [sl._tile_image_area(t[2] * w, t[3] * h, a[i], L) for i, t in enumerate(tiles)]


def fill_of(tiles, w=W, h=H, aspects=None):
    return sum(picture_areas(tiles, w, h, aspects)) / (w * h)


def cold():
    sl._LAYOUT_CACHE.clear()
    sl._LAYOUT_TREE_CACHE.clear()


# ── 1. The canvas is filled ─────────────────────────────────────────────────
def test_fills_the_canvas():
    print("The reported set fills the canvas")
    cold()
    tiles = arrange()
    fill = fill_of(tiles)
    check(fill >= 0.72, "at least 72 % of the canvas carries picture",
          f"{fill * 100:.1f} % (the old search reached 65.0 %)")

    # The tiles must still be one seamless partition — filling the canvas by leaving
    # gaps between the tiles would be no fill at all.
    covered = sum(t[2] * t[3] for t in tiles)
    check(abs(covered - 1.0) < 0.02, "the tiles still cover the whole canvas",
          f"{covered * 100:.1f} % of it")
    over = 0.0
    for i, j in itertools.combinations(range(len(tiles)), 2):
        ax, ay, aw, ah = tiles[i]
        bx, by, bw, bh = tiles[j]
        ox = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
        oy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
        over += ox * oy
    check(over < 1e-6, "no two tiles overlap", f"{over * W * H:.0f} px^2")


# ── 2. The order handed in does not change the answer ───────────────────────
def test_order_does_not_matter():
    print("The order the cameras are handed in changes nothing")
    cold()
    base = arrange()
    base_fill = fill_of(base)
    worst_fill, differ = base_fill, 0
    # A spread of orders, not neighbours of each other: reversed, rotated, sorted by
    # size, and a scattering of the 720.
    orders = [list(reversed(range(6))), [2, 5, 0, 3, 1, 4], [5, 4, 3, 2, 1, 0],
              sorted(range(6), key=lambda i: ASPECTS[i]),
              sorted(range(6), key=lambda i: -weights()[i])]
    orders += [list(p) for p in itertools.islice(itertools.permutations(range(6)), 0, 720, 97)]
    shapes_base = sorted((round(t[2], 6), round(t[3], 6)) for t in base)
    areas_base = picture_areas(base)
    worst_area, worst_shape = 0.0, 0.0
    for o in orders:
        sl._LAYOUT_CACHE.clear()
        got = arrange(o)
        worst_fill = min(worst_fill, fill_of(got))
        for (bw, bh), (gw, gh) in zip(shapes_base,
                                      sorted((round(t[2], 6), round(t[3], 6)) for t in got)):
            worst_shape = max(worst_shape, abs(gw - bw) / bw, abs(gh - bh) / bh)
        for was, now in zip(areas_base, picture_areas(got)):
            worst_area = max(worst_area, abs(now - was) / max(1.0, was))
    check(worst_fill >= base_fill - 1e-6,
          "no order comes out with less picture than another",
          f"worst {worst_fill * 100:.1f} %, best {base_fill * 100:.1f} %")
    # The canvas is cut into the same tiles whatever the order — to within the last
    # percent, which is where the search itself stops telling two arrangements apart.
    check(worst_shape < 0.02, "every order cuts the canvas into the same tiles",
          f"worst tile edge off by {worst_shape * 100:.1f} %")
    # Which camera lands in which tile still follows the list handed in — that is the
    # tie-break, and it is wanted. What must not change is the deal each camera gets.
    check(worst_area < 0.02, "every camera gets the same picture whatever the order",
          f"worst camera off by {worst_area * 100:.1f} %")
    del differ


# ── 3. A size press and Auto-arrange agree ──────────────────────────────────
def test_two_buttons_agree():
    print("A size press and Auto-arrange show the same thing")
    from datetime import date                                   # noqa: PLC0415
    from PySide6.QtWidgets import QApplication                   # noqa: PLC0415
    app = QApplication.instance() or QApplication([])

    for c, k in zip(CAMS, SIZES):
        sl.set_cam_size_class(c, k)

    # A remembered HAND-MADE arrangement whose camera order is not the picked order —
    # this is what seeded the size button with a different packing order.
    hand_order = list(reversed(CAMS))
    tiles = [[0.0, 0.0, 0.5, 0.5], [0.5, 0.0, 0.5, 0.5], [0.0, 0.5, 0.25, 0.5],
             [0.25, 0.5, 0.25, 0.5], [0.5, 0.5, 0.25, 0.5], [0.75, 0.5, 0.25, 0.5]]
    sl.CamLayoutStore._write_entry(
        sl.CamLayoutStore.key_for(CAMS),
        {"auto": False, "tiles": tiles, "cam_order": hand_order})

    dlg = sl.CameraPickerDialog(
        date.today(), 8, 19, list(CAMS), None,
        preloaded_cameras=[(str(i + 1), c) for i, c in enumerate(CAMS)],
        multi_grid=None, windows=None, cam_area_px=(int(W), int(H)),
        label_font_px=20, show_layout=True)
    dlg.show()
    for _ in range(6):
        app.processEvents()
    check(list(dlg._board._pack_order) != list(CAMS),
          "the board really did open on the remembered order",
          " ".join(n.split("-")[2] for n in dlg._board._pack_order))

    dlg._on_size_changed(CAMS[5], "large")
    for _ in range(4):
        app.processEvents()
    after_size = {nm: list(t) for nm, t in zip(dlg._board._cam_names, dlg._board._tiles)}

    dlg._on_auto_arrange()
    for _ in range(4):
        app.processEvents()
    after_auto = {nm: list(t) for nm, t in zip(dlg._board._cam_names, dlg._board._tiles)}

    same = all(max(abs(x - y) for x, y in zip(after_size[nm], after_auto[nm])) < 1e-6
               for nm in after_size)
    worst = max((max(abs(x - y) for x, y in zip(after_size[nm], after_auto[nm]))
                 for nm in after_size), default=0.0)
    check(same, "both buttons put every camera in the same tile",
          f"worst difference {worst * 100:.2f} % of the canvas")
    dlg.close()


# ── 4. Nothing is drawn at random ───────────────────────────────────────────
def test_repeatable():
    print("The same cameras always give the same arrangement")
    cold()
    first = arrange()
    cold()
    second = arrange()
    same = max(abs(a[k] - b[k]) for a, b in zip(first, second) for k in range(4)) < 1e-12
    check(same, "two runs from cold are identical")
    # And the pool it searches is the same list every time.
    p1 = sl._layout_order_pool(ASPECTS, weights())
    p2 = sl._layout_order_pool(ASPECTS, weights())
    check(p1 == p2, "the orders searched are the same list every time",
          f"{len(p1)} orders")


# ── 5. The sizes are still honoured ─────────────────────────────────────────
def test_sizes_still_honoured():
    print("The sizes asked for are still what comes out")
    cold()
    tiles = arrange()
    areas = picture_areas(tiles)
    wt = weights()
    check(sl._size_order_kept(areas, wt),
          "no frame is bigger than one that asked for a bigger size",
          " ".join(f"{int(w)}->{a / 1000:.0f}k" for w, a in zip(wt, areas)))
    miss = sl._size_mismatch(areas, wt)
    check(miss <= 1.15, "the frames come out at the ratio the sizes asked for",
          f"worst frame off by {int((miss - 1) * 100)} %")

    # A Largest against a Smallest, which is the extreme the labels promise.
    cold()
    four = arrange(aspects=[1.0] * 4, wts=[16.0, 8.0, 4.0, 1.0])
    ar = picture_areas(four, aspects=[1.0] * 4)
    ratio = ar[0] / max(1e-9, ar[3])
    check(14.0 <= ratio <= 18.0, "Largest still gets sixteen times Smallest",
          f"{ratio:.1f}x")


# ── 6. Built once per camera set ────────────────────────────────────────────
def test_built_once_per_set():
    print("The arrangements are built once, not once per window size")
    cold()
    calls = {"n": 0}
    real = sl._layout_trees

    def counted(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    sl._layout_trees = counted
    try:
        arrange()
        built = calls["n"]
        check(built > 8, "the search really does try many orders", f"{built} orders")
        calls["n"] = 0
        t0 = time.perf_counter()
        for step in range(1, 9):        # the window being dragged
            arrange(w=W + step * 40, h=H + step * 20)
        dt = (time.perf_counter() - t0) * 1000.0
        check(calls["n"] == 0, "eight more window sizes rebuild nothing",
              f"{calls['n']} rebuilds, {dt:.0f} ms for the eight")
        check(dt < 8 * 200.0, "a resize stays well under a fifth of a second per size",
              f"{dt / 8:.0f} ms per size")
    finally:
        sl._layout_trees = real

    # A size change is a different camera set as far as the pool goes: it must rebuild.
    calls["n"] = 0
    sl._layout_trees = counted
    try:
        arrange(wts=[4.0, 4.0, 8.0, 8.0, 8.0, 16.0])
        check(calls["n"] > 0, "changing a size builds the arrangements again",
              f"{calls['n']} orders")
    finally:
        sl._layout_trees = real


def main():
    test_fills_the_canvas()
    test_order_does_not_matter()
    test_two_buttons_agree()
    test_repeatable()
    test_sizes_still_honoured()
    test_built_once_per_set()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
