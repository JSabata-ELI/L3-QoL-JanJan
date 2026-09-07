"""Camera size classes, and whether the arrangement board really is a model of the grid.

Four claims are checked, each one a bug that was actually shipped:

  * **Sizes reach the packer.** Setting a camera to Largest and another to Smallest must
    come out as roughly the 16 : 1 image-AREA ratio the labels promise, and the tiles must
    still be a seamless partition of the canvas — no gaps, no overlaps, every edge shared.
    (Half of "use every centimetre" is that the areas are honoured; the other half is that
    nothing is left empty between them.)

  * **Defaults change nothing.** With no size ever set, the weights must be exactly the
    old hard-coded ones — Medium (8) everywhere, Largest (16) for a diode array, i.e. the
    same 2 : 1 the regex used to return. Anything else silently rearranges every existing
    camera set on upgrade.

  * **The board matches the grid.** The board's fractions must be IDENTICAL to the ones
    the live grid computes for the same cameras — this is the point of computing the
    arrangement at live size instead of board size. It is checked at three board sizes,
    because the old code recomputed on the board and so gave a different answer for each.

  * **The header keeps its proportion.** The name bar's share of a tile must be the same
    on the board as in the grid, within a pixel of rounding. The old board reserved the
    grid's ABSOLUTE header height on a board a third of the size, so the bar came out
    several times too fat — the complaint this test exists for.

Rendering is done with the real windows platform: offscreen has no fonts, reports wrong
text sizes, and would make the header measurement meaningless.

Not shipped: the builder keeps test_* out of the bundle.

Usage:

    python testing/test_cam_size_layout.py
    python testing/test_cam_size_layout.py --shot out.png     # also save a screenshot

Exit code is 1 when a claim fails, so it can gate a build.
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A scratch APPDATA so the test never touches the real cam_sizes.json / cam_layouts.json.
_TMP = tempfile.mkdtemp(prefix="eli_size_test_")
os.environ["APPDATA"] = _TMP

import is_t as sl                                    # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402


FAILURES: list = []


def check(ok: bool, what: str, detail: str = ""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  — " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)


# ── 1. Sizes reach the packer ───────────────────────────────────────────────
def test_weights_and_areas():
    print("Sizes reach the packer")
    cams = ["C03-001-AAA1FF-IMG", "C03-002-BBB1FF-IMG",
            "C03-003-CCC1FF-IMG", "C03-004-DDD1FF-IMG"]
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    sl.set_cam_size_class(cams[0], "largest")
    sl.set_cam_size_class(cams[3], "smallest")
    check(sl.cam_size_class(cams[0]) == "largest", "Largest is stored and read back")
    check(abs(sl._cam_layout_weight(cams[0]) - 16.0) < 1e-9, "Largest weighs 16")
    check(abs(sl._cam_layout_weight(cams[3]) - 1.0) < 1e-9, "Smallest weighs 1")

    W, H, L = 1600, 900, 41
    aspects = [1.0] * 4
    weights = [sl._cam_layout_weight(c) for c in cams]
    entries = sl.compute_camera_layout(aspects, W, H, L, weights)
    check(len(entries) == 4, "four tiles come back")

    areas = [sl._tile_image_area(e.w * W, e.h * H, aspects[i], L)
             for i, e in enumerate(entries)]
    ratio = areas[0] / max(1e-9, areas[3])
    # A sized set is cut to the sizes asked for (_fit_tiles_to_sizes), so the realised
    # ratio is 16 to within the correction rounds and whole pixels — not merely "more".
    check(14.0 <= ratio <= 18.0,
          "Largest gets sixteen times the picture area of Smallest",
          f"ratio {ratio:.1f}x (asked 16x)")
    check(areas[0] > areas[1] > areas[3],
          "areas fall in the order the sizes were set",
          f"{areas[0]:.0f} > {areas[1]:.0f} > {areas[3]:.0f}")

    # Seamless partition: total tile area == canvas, and no two tiles overlap.
    rects = [sl._entry_rect(e.x, e.y, e.w, e.h, W, H) for e in entries]
    covered = sum(r.width() * r.height() for r in rects)
    check(abs(covered - W * H) <= 4 * (W + H),
          "the tiles cover the whole canvas",
          f"{100.0 * covered / (W * H):.1f}% of it")
    overlap = 0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            inter = rects[i].intersected(rects[j])
            overlap += max(0, inter.width()) * max(0, inter.height())
    check(overlap == 0, "no two tiles overlap", f"{overlap} px^2")


# ── 2. Defaults change nothing ──────────────────────────────────────────────
def test_defaults_match_old_behaviour():
    print("Defaults reproduce the old hard-coded weights")
    plain = "C03-050-ZZZ9FF-IMG"          # never set
    diode = "C03-051-PD2M1XDF-IMG"        # never set, portrait diode array
    check(sl.cam_size_class(plain) == "medium", "an unset camera is Medium")
    check(sl.cam_size_class(diode) == "largest", "an unset diode array is Largest")
    w_plain, w_diode = sl._cam_layout_weight(plain), sl._cam_layout_weight(diode)
    check(abs(w_diode / w_plain - 2.0) < 1e-9,
          "diode array still asks for exactly twice a normal camera",
          f"{w_diode} / {w_plain}")
    # Same arrangement as the old 1.0 / 2.0 pair would have produced: only ratios matter.
    a = [1.0, 0.45, 1.0]
    names = [plain, diode, "C03-052-YYY1FF-IMG"]
    new = sl.compute_camera_layout(a, 1600, 900, 41,
                                   [sl._cam_layout_weight(n) for n in names])
    sl._LAYOUT_CACHE.clear()
    old = sl.compute_camera_layout(a, 1600, 900, 41, [1.0, 2.0, 1.0])
    check(_same(new, old), "the arrangement is byte-for-byte the old one")

    # All-equal sizes must go down the untouched path: same answer as passing no
    # weights at all, and the tight tolerance, not the loose one used for set sizes.
    sl._LAYOUT_CACHE.clear()
    none_w = sl.compute_camera_layout(a, 1600, 900, 41, None)
    sl._LAYOUT_CACHE.clear()
    med_w = sl.compute_camera_layout(a, 1600, 900, 41, [8.0, 8.0, 8.0])
    check(_same(none_w, med_w), "all-Medium is the same as no sizes at all")

    # The case the tight tolerance exists for: three landscape cameras must NOT come
    # out as one row across the top with two thirds of the canvas empty.
    sl._LAYOUT_CACHE.clear()
    three = sl.compute_camera_layout([1.78, 1.78, 1.78], 1600, 900, 41, None)
    one_row = all(abs(e.h - 1.0) < 1e-6 for e in three)
    check(not one_row, "three landscape cameras are not squeezed into one row",
          f"heights {[round(e.h, 3) for e in three]}")


# ── 2b. Every size class really is the size it says ─────────────────────────
def test_sizes_are_honoured():
    """The complaint this pass exists for: with sizes set, the frames came out in the
    wrong ORDER — Smallest was the second biggest frame on screen and Medium one of
    the two smallest. The cause was that the arrangement was cut from the SHAPES of
    the pictures only, so the sizes could not change any tile; they merely picked
    between arrangements that were all near-equal. Every case below is measured on
    the picture actually reached inside each tile."""
    print("Every size class is the size it says")
    L = 41
    cases = [
        ("the reported case, 4 square cameras", [1.0] * 4, [16.0, 1.0, 8.0, 4.0], 1600, 900),
        ("all five classes at once", [1.0] * 5, [16.0, 12.0, 8.0, 4.0, 1.0], 1600, 900),
        ("Largest next to Smallest", [1.0, 1.0], [16.0, 1.0], 1600, 900),
        ("a portrait diode among landscape", [1.0, 0.45, 1.0], [16.0, 8.0, 8.0], 1600, 900),
        ("wide cameras, one Largest", [1.78] * 4, [16.0, 4.0, 4.0, 4.0], 1600, 900),
        ("a tall canvas", [1.0] * 4, [16.0, 1.0, 8.0, 4.0], 700, 900),
        ("a very wide canvas", [1.0] * 4, [16.0, 1.0, 8.0, 4.0], 2400, 600),
        ("eight cameras, mixed shapes", [1.0, 0.45, 1.78, 1.0, 1.0, 1.33, 0.45, 1.0],
         [16.0, 8.0, 8.0, 4.0, 8.0, 12.0, 16.0, 1.0], 1600, 900),
    ]
    for what, aspects, weights, W, H in cases:
        sl._LAYOUT_CACHE.clear()
        ent = sl.compute_camera_layout(list(aspects), W, H, L, list(weights))
        areas = [sl._tile_image_area(e.w * W, e.h * H, aspects[i], L)
                 for i, e in enumerate(ent)]
        check(sl._size_order_kept(areas, weights),
              f"{what}: no frame is bigger than a frame that asked for more",
              " ".join(f"{w:.0f}->{a/1000:.0f}k" for w, a in zip(weights, areas)))
        # ... and not merely in the right order: within a tenth of the asked ratio.
        miss = sl._size_mismatch(areas, weights)
        check(miss <= 1.10, f"{what}: the asked ratios are reached",
              f"worst frame off by {100 * (miss - 1.0):.0f}%")


def _same(x, y) -> bool:
    return all(abs(p.x - q.x) < 1e-9 and abs(p.y - q.y) < 1e-9 and
               abs(p.w - q.w) < 1e-9 and abs(p.h - q.h) < 1e-9
               for p, q in zip(x, y))


# ── 3 + 4. The board is a model of the grid ─────────────────────────────────
def test_board_matches_grid(shot: "str | None"):
    print("The board matches the live grid")
    app = QApplication.instance() or QApplication([])

    cams = ["C03-010-AAA1FF-IMG", "C03-011-PD1M1XDF-IMG",
            "C03-012-CCC1FF-IMG", "C03-013-DDD1FF-IMG", "C03-014-EEE1FF-IMG"]
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    sl.set_cam_size_class(cams[0], "largest")
    sl.set_cam_size_class(cams[4], "small")

    live_w, live_h, label_px, font_px = 1600, 880, 41, 20
    aspects = [sl._cam_aspect_hint(c) for c in cams]
    weights = [sl._cam_layout_weight(c) for c in cams]
    grid_entries = sl.compute_camera_layout(aspects, live_w, live_h, label_px, weights)

    board = sl._LayoutCanvasWidget(cam_names=cams, aspects=aspects, label_px=label_px,
                                   canvas_px=(live_w, live_h), label_font_px=font_px)
    # Three very different board sizes — the old code recomputed the arrangement on the
    # board, so each of these gave a different (and wrong) answer.
    for bw, bh in ((520, 300), (760, 460), (1100, 640)):
        board.resize(bw, bh)
        board.show()
        app.processEvents()
        got = board.get_entries()
        same = all(abs(g.x - e.x) < 1e-6 and abs(g.y - e.y) < 1e-6 and
                   abs(g.w - e.w) < 1e-6 and abs(g.h - e.h) < 1e-6
                   for g, e in zip(got, grid_entries))
        check(same, f"board {bw}x{bh} shows the grid's own fractions")

        # Header proportion, per tile, against what the grid gives the same tile.
        worst = 0.0
        for i in range(len(cams)):
            r = board._tile_rect(i)
            hdr_share = board._header_h(r) / max(1, r.height())
            live_tile_h = grid_entries[i].h * live_h
            live_share = (label_px - board._LIVE_M_BOTTOM) / max(1.0, live_tile_h)
            worst = max(worst, abs(hdr_share - live_share))
        # One board pixel of rounding, expressed as a share of the smallest tile.
        tol = 1.5 / max(1.0, min(board._tile_rect(i).height() for i in range(len(cams))))
        check(worst <= tol,
              f"board {bw}x{bh} keeps the header proportion",
              f"worst off by {100 * worst:.2f} pp, allowed {100 * tol:.2f} pp")

        # And the frame must not be pushed out of its window by a double-counted margin.
        for i in range(len(cams)):
            r = board._tile_rect(i)
            img = board._image_rect_in(r, aspects[i])
            if not r.contains(img):
                check(False, f"board {bw}x{bh} keeps the frame inside its window",
                      f"tile {i}: {r} vs image {img}")
                break
        else:
            check(True, f"board {bw}x{bh} keeps every frame inside its window")

    if shot:
        board.resize(900, 520)
        app.processEvents()
        board.grab().save(shot)
        print(f"  screenshot written to {shot}")
    board.hide()


# ── 5. The real grid lands where the board said ─────────────────────────────
def test_real_grid_matches_board(shot: "str | None"):
    """The acceptance test for the whole thing: build the REAL tiles in the REAL
    auto-layout container at a known size, then build the board as a model of that
    same area, and compare tile rectangles scaled to one another."""
    print("The real grid lands where the board said")
    from PySide6.QtWidgets import QWidget, QVBoxLayout   # noqa: PLC0415
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; } "
                      "QLabel { background: transparent; }")

    cams = ["C03-020-AAA1FF-IMG", "C03-021-PD3M1XDF-IMG",
            "C03-022-CCC1FF-IMG", "C03-023-DDD1FF-IMG"]
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    sl.set_cam_size_class(cams[0], "largest")
    sl.set_cam_size_class(cams[3], "small")

    live_w, live_h, font_px = 1500, 820, 20
    host = QWidget()
    host.resize(live_w, live_h)
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    views = [sl.CameraView(i, c, host) for i, c in enumerate(cams)]
    for v in views:
        v.set_label_font_size(font_px)
    cont = sl._AutoLayoutContainer(views, parent=host)
    hl.addWidget(cont)
    host.show()
    for _ in range(8):
        app.processEvents()

    label_px = views[0].image_overhead_px()
    board = sl._LayoutCanvasWidget(
        cam_names=cams, aspects=[sl._cam_aspect_hint(c) for c in cams],
        label_px=label_px, canvas_px=(cont.width(), cont.height()),
        label_font_px=font_px)
    board.resize(820, int(820 * cont.height() / cont.width()))
    board.show()
    for _ in range(6):
        app.processEvents()

    b = board._board()
    sx = b.width() / cont.width()
    sy = b.height() / cont.height()
    worst = 0.0
    for i, v in enumerate(views):
        live = v.geometry()
        j = board._cam_names.index(v.cam_name)
        prev = board._tile_rect(j)
        prev.translate(-b.left(), -b.top())
        for got, want in ((prev.left(), live.left() * sx),
                          (prev.top(), live.top() * sy),
                          (prev.width(), live.width() * sx),
                          (prev.height(), live.height() * sy)):
            worst = max(worst, abs(got - want))
    check(worst <= 2.0, "every tile sits where the board drew it",
          f"worst edge off by {worst:.1f} board px")

    # And the header proportion, tile by tile, board against the real widget.
    worst_h = 0.0
    for i, v in enumerate(views):
        live = v.geometry()
        live_share = (label_px - board._LIVE_M_BOTTOM) / max(1, live.height())
        j = board._cam_names.index(v.cam_name)
        r = board._tile_rect(j)
        worst_h = max(worst_h, abs(board._header_h(r) / max(1, r.height()) - live_share))
    check(worst_h <= 0.02, "the name bar keeps its share of the tile",
          f"worst off by {100 * worst_h:.2f} percentage points")

    if shot:
        p = Path(shot)
        host.grab().save(str(p.with_name(p.stem + "_grid" + p.suffix)))
        board.grab().save(str(p.with_name(p.stem + "_board" + p.suffix)))
        print(f"  grid + board screenshots written next to {shot}")
    host.hide()
    board.hide()


# ── 6. Sizes survive a preset round trip, and the two picker modes ──────────
def test_picker_modes_and_presets():
    """A preset must carry the sizes and win when loaded; and the picker reused by the
    tabs that have NO camera grid must show no Layout section and touch nothing."""
    print("Presets carry the sizes; the no-grid mode changes nothing")
    from datetime import date                              # noqa: PLC0415
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")

    cams = ["C03-030-AAA1FF-IMG", "C03-031-BBB1FF-IMG", "C03-032-CCC1FF-IMG"]
    wanted = {cams[0]: "largest", cams[1]: "medium", cams[2]: "smallest"}
    for c, k in wanted.items():
        sl.set_cam_size_class(c, k)

    def picker(show=True, picked=None):
        return sl.CameraPickerDialog(
            date.today(), 8, 19, picked if picked is not None else cams, None,
            preloaded_cameras=[(str(i + 1), c) for i, c in enumerate(cams)],
            multi_grid=None, windows=None, cam_area_px=(1600, 880),
            label_font_px=20, show_layout=show)

    dlg = picker()
    dlg.show()
    for _ in range(6):
        app.processEvents()
    check(dlg._layout_box.isVisible(), "the Layout section is there without a second click")
    check(dlg._size_row._btns and not next(iter(dlg._size_row._btns.values())).isEnabled(),
          "the size buttons wait for a camera to be clicked")
    dlg._board._selected = 0
    dlg._size_row.set_camera(dlg._board._cam_names[0])
    check(next(iter(dlg._size_row._btns.values())).isEnabled(),
          "clicking a camera wakes the size buttons")
    check(dlg._board_is_auto(), "a plain click leaves the arrangement automatic")
    # Save a preset, then wipe the sizes and load it back.
    dlg._presets["sizetest"] = {"cameras": list(cams)}
    dlg._presets["sizetest"].update(dlg._capture_current_layout())
    saved_sizes = dlg._presets["sizetest"].get("sizes")
    check(saved_sizes == wanted, "the preset stores every picked camera's size",
          str(saved_sizes))
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    dlg._refresh_preset_list()
    for r in range(dlg._preset_list.rowCount()):
        if dlg._preset_list.item(r, 0).text() == "sizetest":
            dlg._preset_list.selectRow(r)
            break
    for _ in range(4):
        app.processEvents()
    back = {c: sl.cam_size_class(c) for c in cams}
    check(back == wanted, "loading the preset brings its sizes back", str(back))
    dlg.close()

    # The no-grid mode: no Layout section, old window size, and OK writes nothing.
    before = sl.CamLayoutStore.load_raw(cams)
    d2 = picker(show=False)
    d2.show()
    for _ in range(6):
        app.processEvents()
    check(not d2._layout_box.isVisible(), "no Layout section where there is no grid")
    check((d2.width(), d2.height()) == (660, 640),
          "the no-grid window keeps its old size", f"{d2.width()}x{d2.height()}")
    d2._on_accept()
    check(sl.CamLayoutStore.load_raw(cams) == before,
          "the no-grid mode leaves the stored arrangement alone")
    check(d2.layout_config is None, "the no-grid mode returns no arrangement")
    d2.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", help="save a screenshot of the board to this file")
    args = ap.parse_args()

    test_weights_and_areas()
    test_defaults_match_old_behaviour()
    test_sizes_are_honoured()
    test_board_matches_grid(args.shot)
    test_real_grid_matches_board(args.shot)
    test_picker_modes_and_presets()

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
