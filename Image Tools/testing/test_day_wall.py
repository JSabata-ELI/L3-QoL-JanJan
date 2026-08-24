"""Assert the day wall compares days HONESTLY.

The archive does not store camera counts. It stretches every frame's own power-of-two
bracket up into the 16-bit container, so a dim day and a bright one can BOTH sit near
51 000 in the file. Anything that renders `stored / 65535` therefore paints every day at
almost the same brightness — which is the one thing a comparison view must not do.

Two bugs of exactly that shape were found here and are what this test pins:

  * the shared Auto pass pooled the STORED values of every day to take one percentile
    window. Stored 51299 is 400 counts on a dim day and 3200 on a bright one, so the
    pool mixed units; the pair that came out (contrast 127, the maximum) saturated every
    brighter day to white — code peaks 94 / 255 / 255.
  * the deviation from a reference day subtracted the STORED arrays. Day 1 stored 51299
    and day 3 stored 51212, so |day3 - day1| came out at code 0 — a black tile — while
    the real difference was 2800 counts.

Both are fixed by bringing each day onto one common range BEFORE pooling or subtracting.

Frames are written the way the archiver writes them:

    stored = counts * SCALE_FACTORS[bits]        MaxValue tEXt = counts peak

so three days of very different true strength must come out at code peaks proportional
to their COUNTS, not all pinned near white.

Runs offscreen against synthetic local frames — no share, no network:

    python testing/test_day_wall.py
"""
import importlib.util
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def load_finder():
    """Load if_t.py as `image_finder`, the way main.py does — slider FIRST, so the
    Finder borrows that instance instead of exec'ing is_t.py a second time."""
    B.load_slider()
    if "image_finder" in sys.modules:
        return sys.modules["image_finder"]
    argv, sys.argv = sys.argv, ["if_t.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "image_finder", str(B.HERE / "if_t.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["image_finder"] = m
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = argv


CAM = "C03-040-PTM11WNF-_-IMG"
COUNTS_PEAKS = [400, 1600, 3200]        # true sensor counts on three different days


def write_archive_frames(cam_dir: Path, ims) -> "tuple[list, list, list]":
    import numpy as np
    from PIL import Image as PilImage, PngImagePlugin
    cells, expected, stored_peaks = [], [], []
    for i, cpk in enumerate(COUNTS_PEAKS):
        yy, xx = np.mgrid[0:120, 0:160]
        counts = np.exp(-(((xx - 80) ** 2 + (yy - 60) ** 2) / 900.0)) * cpk
        bits = ims.bits_from_max_value(cpk)
        stored = np.clip(np.rint(counts * ims.SCALE_FACTORS[bits]),
                         0, 65535).astype(np.uint16)
        p = cam_dir / f"{CAM}_-_{1_700_000_000_000_000_000 + i}.png"
        meta = PngImagePlugin.PngInfo()
        meta.add_text("MaxValue", str(int(cpk)))
        PilImage.fromarray(stored).save(p, pnginfo=meta)
        stored_peaks.append(int(stored.max()))
        expected.append(255.0 * cpk / ims.SENSOR_FULL_SCALE_COUNTS)
        cells.append({"day": date(2026, 8, 10 + i), "cam": "C03-040-PTM11WNF", "path": p,
                      "ts_ns": 1_700_000_000_000_000_000 + i, "status": "found"})
    return cells, expected, stored_peaks


def main():
    import numpy as np
    from PIL import Image as PilImage
    from PySide6.QtWidgets import QApplication

    _if = load_finder()
    ims = sys.modules["img_scale"]
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="if_wall_"))
    cam_dir = tmp / CAM
    cam_dir.mkdir(parents=True)
    cells, expected, stored_peaks = write_archive_frames(cam_dir, ims)

    print(f"\nstored peaks in the files: {stored_peaks}")
    print("  nearly identical — 'stored / 65535' cannot tell these days apart")

    w = _if.ImageFinderWidget()
    wall = w._wall
    wall.resize(1200, 800)
    wall.set_cells(cells)
    end = time.monotonic() + 20
    while len(wall._raw) < len(cells) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)

    print("\ntrue intensity reaches the screen")
    check("every frame was read", len(wall._raw) == len(cells),
          f"{len(wall._raw)}/{len(cells)}")
    got = []
    for i, c in enumerate(cells):
        arr, fs = wall._raw[c["path"]]
        got.append(int(_if._render_u8(arr, False, fs, None, 0, 0).max()))
    for i, (g, e) in enumerate(zip(got, expected)):
        check(f"day {i + 1} renders at its true counts", abs(g - e) <= 3,
              f"code {g}, expected ~{e:.0f}")
    check("a stronger day reads brighter", got[0] < got[1] < got[2], f"{got}")
    ratio = got[2] / max(1, got[0])
    check("brightness ratio matches reality",
          abs(ratio - COUNTS_PEAKS[2] / COUNTS_PEAKS[0]) < 1.0,
          f"{ratio:.1f}x shown vs {COUNTS_PEAKS[2] / COUNTS_PEAKS[0]:.1f}x true")
    naive = [int(255.0 * s / 65535.0) for s in stored_peaks]
    check("beats the naive stored/65535 mapping",
          max(naive) - min(naive) < max(got) - min(got),
          f"naive would show {naive}")

    print("\nAuto stays comparable (one shared pair, not one per tile)")
    wall.set_display("Grayscale", True, None, 0, 0)
    pair = wall._shared_auto_pair()
    auto = []
    for c in cells:
        arr, fs = wall._raw[c["path"]]
        auto.append(int(_if._render_u8(arr, False, fs, None, pair[0], pair[1]).max()))
    check("Auto keeps the days distinguishable", auto[0] < auto[1] < auto[2],
          f"contrast={pair[0]} offset={pair[1]} -> {auto}")
    check("Auto actually brightens", max(auto) > max(got), f"{max(auto)} > {max(got)}")

    print("\nlayout")
    wall.set_display("Grayscale", False, None, 0, 0)
    wall._relayout()
    rects = wall._rects
    area = sum(r.width() * r.height() for r in rects)
    canvas = wall.width() * wall.height()
    check("one tile per day", len(rects) == len(cells), f"{len(rects)}")
    check("tiles are a seamless partition of the canvas", area > 0.97 * canvas,
          f"{100.0 * area / canvas:.1f}% covered")

    def tile_peak_of(wl, i):
        """Brightest pixel a given wall actually PAINTS for tile i."""
        pm = wl._tile_pixmap(i, wl._rects[i])
        if pm is None or pm.isNull():
            return -1
        im = pm.toImage()
        return max(im.pixelColor(x, y).value()
                   for y in range(0, im.height(), 3)
                   for x in range(0, im.width(), 3))

    def tile_peak(i):
        """Brightest pixel the wall actually PAINTS — measured through the real render
        path rather than a re-implementation of it that could drift."""
        pm = wall._tile_pixmap(i, rects[i])
        if pm is None or pm.isNull():
            return -1
        im = pm.toImage()
        return max(im.pixelColor(x, y).value()
                   for y in range(0, im.height(), 3)
                   for x in range(0, im.width(), 3))

    print("\ndeviation from a reference day")
    plain2, plain0 = tile_peak(2), tile_peak(0)
    wall.set_baseline(0)
    diff2, ref_tile = tile_peak(2), tile_peak(0)
    expected_diff = 255.0 * (COUNTS_PEAKS[2] - COUNTS_PEAKS[0]) / ims.SENSOR_FULL_SCALE_COUNTS
    check("the difference is a real picture, not a black tile", diff2 > 20,
          f"code {diff2}")
    check("the difference is the TRUE difference", abs(diff2 - expected_diff) <= 4,
          f"code {diff2}, expected ~{expected_diff:.0f}")
    check("the reference tile itself is left alone", ref_tile == plain0,
          f"{ref_tile} vs {plain0}")
    wall.set_baseline(None)

    print("\nsaving the whole wall as one picture")
    comp = wall.composite_image(scale=2.0)
    check("composite is produced", comp is not None and comp.width() > 0,
          f"{comp.width()}x{comp.height()}" if comp is not None else "None")

    print("\nhandoff to the Image Slider")

    class _FakeSlider:
        def __init__(self):
            self.folder = None
            self.emap = None

        def receive_external_folder(self, folder, energy_map=None, discrete=True,
                                    cam_name=None):
            self.folder = Path(folder)
            self.emap = dict(energy_map or {})
            return True

    class _FakeTabs:
        def setCurrentIndex(self, i):
            pass

    w._slider_ref, w._tab_widget = _FakeSlider(), _FakeTabs()
    w._push_cells_to_slider(cells)
    sent = w._slider_ref.folder
    # img_scale.camera_from_path reads the camera off the PARENT FOLDER, so a flat temp
    # folder would strip it and the Slider would fall back to the plain 65535 range —
    # the same frame ~16x darker over there than on the wall it was compared on.
    check("the handed-over folder is still a camera folder",
          sent is not None and ims.camera_from_path(sent / "x.png") == "C03-040-PTM11WNF",
          sent.name if sent else "None")
    check("every picked frame was handed over",
          sent is not None and len(list(sent.iterdir())) == len(cells))
    check("every frame kept its own caption", len(w._slider_ref.emap) == len(cells))
    same = True
    for c in cells:
        dst = sent / Path(c["path"]).name
        with PilImage.open(dst) as pil:
            mode, info = pil.mode, dict(pil.info or {})
        if ims.full_scale_for_pil(dst, info, mode) != wall._raw[c["path"]][1]:
            same = False
    check("handed-over frames render on the wall's scale", same)

    print("\ntabs: One frame, one per camera, then day by day")
    results = {
        "C03-040-PTM11WNF-_-IMG": [(c["day"], 8, c["path"],
                                    {"sbw4": 1.0, "source": "sbw4"}, "found")
                                   for c in cells],
    }
    w.fill_wall(results, None)
    for _ in range(50):
        app.processEvents()
        time.sleep(0.01)
    titles = [w._view_tabs.tabText(i) for i in range(w._view_tabs.count())]
    check("the close-up is the first tab", titles[:1] == ["One frame"], str(titles))
    check("there is a tab for the camera", "C03-040-PTM11WNF" in titles, str(titles))
    check("day by day is last", titles[-1] == "Day by day", str(titles))
    check("no pop-up window class survives",
          not hasattr(_if, "MultiDayPreviewWindow"))
    cam_wall = w._cam_walls["C03-040-PTM11WNF"]
    cam_wall.resize(1200, 800)
    cam_wall._relayout()
    check("the camera tab holds every day", len(cam_wall.cells()) == len(cells),
          f"{len(cam_wall.cells())}")

    print("\nthe frame is read from the share ONCE for all the tabs")
    check("the walls share one frame cache",
          cam_wall._raw is w._day_wall._raw is w._wall_shared.raw)

    print("\nday by day: a row per day, tall enough to scroll")
    dw = w._day_wall
    dw.resize(900, 500)
    dw._relayout()
    check("one banner per day", len(dw._row_heads) == len(cells),
          f"{len(dw._row_heads)}")
    check("the banner names the day",
          dw._row_heads[0][1].startswith("Monday") and "10.08.2026" in dw._row_heads[0][1],
          dw._row_heads[0][1])
    check("the wall asks for the height its rows need",
          dw.minimumHeight() >= dw.rows_content_height() > 0,
          f"min {dw.minimumHeight()} vs {dw.rows_content_height()}")
    ys = [dw._rects[i].y() for i in range(len(cells))]
    check("each day sits below the previous one", ys == sorted(ys) and len(set(ys)) == 3,
          str(ys))

    print("\npicking one frame and opening it up")
    target = cells[0]["path"]
    w._wall = cam_wall
    cam_wall.set_selected(target, True)
    check("one frame is selected", w._wall_shared.sel == {target})
    check("the count is shown", "1 frame selected" in w._sel_wall_lbl.text(),
          w._sel_wall_lbl.text())
    check("only that frame is aimed at", w._wall_target_paths() == [target])

    before = [tile_peak_of(cam_wall, i) for i in range(len(cells))]
    cam_wall.apply_adjust([target], 0, 60, None)
    after = [tile_peak_of(cam_wall, i) for i in range(len(cells))]
    check("the picked frame changed", after[0] > before[0], f"{before[0]} ->{after[0]}")
    check("its neighbours did not", after[1:] == before[1:],
          f"{before[1:]} ->{after[1:]}")
    check("and it says it is no longer comparable",
          "adjusted" in cam_wall._caption(cam_wall.cells()[0]),
          cam_wall._caption(cam_wall.cells()[0]))
    check("the day-by-day tab shows the same adjustment",
          w._day_wall._shared.adj.get(target) is not None)

    w._reset_wall_edits()
    check("Reset puts every frame back on the shared setting",
          [tile_peak_of(cam_wall, i) for i in range(len(cells))] == before)
    w._undo_wall_edit()
    check("Undo brings the adjustment back",
          w._wall_shared.adj.get(target) is not None)
    w._reset_wall_edits()

    print("\nturning a frame")
    h0, w0 = wall._raw[target][0].shape[:2]
    cam_wall.rotate([target], 90)
    pm = cam_wall._tile_pixmap(0, cam_wall._rects[0])
    check("the picture is on its side now",
          pm is not None and (pm.width() < pm.height()) == (w0 > h0),
          f"{pm.width()}x{pm.height()} from {w0}x{h0}")
    w._reset_wall_edits()

    print("\nthe caption says which reading picked the frame")
    check("SBW4 is named", "[SBW4]" in cam_wall._caption(cam_wall.cells()[0]),
          cam_wall._caption(cam_wall.cells()[0]))

    print("\nStop All")
    w.cancel_scan()
    check("cancel_scan exists and runs", True)

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
