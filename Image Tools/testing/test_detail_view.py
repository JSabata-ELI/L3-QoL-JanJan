"""The Detailed view tab: one frame, big, stepped with the arrows.

A click on a tile used to throw the close-up WINDOW over the wall. Marking a tile
is what a click means now, so looking closely needed a place of its own — and the
operator asked for it to be a tab beside the walls, not only a pop-up.

What it shows is THE MARKED FRAMES, or every frame on the wall when nothing is
marked: the same rule the brightness, the palette and the rotation follow
(`_wall_target_paths`). The page itself is built by `_make_frame_page`, the same
call that builds the close-up window's, so the two are one look and one renderer
and they share one index — step forward in the tab and the window is on the same
frame.

Pinned here:
  * the tab exists, and it is the LAST one, after every wall
  * with nothing marked it walks the whole wall
  * marking three tiles narrows it to those three, in the wall's own order
  * the frame being looked at is KEPT when it survives a change of selection
  * the arrows wrap, and the tab title counts
  * left / right arrow keys step it, and only while it is the tab on screen
  * the wall tabs still own `self._wall` — the Detailed view must not become the
    thing `Save view` and the display controls aim at
  * a search rebuilds every wall tab and this page comes through it alive

Runs offscreen against synthetic local frames — no share, no network:

    python testing/test_detail_view.py
"""
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported
from test_day_wall import load_finder

FAILURES: "list[str]" = []
CAM = "C03-040-PTM11WNF-_-IMG"
N = 5


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def write_frames(cam_dir: Path):
    """Five ordinary archive frames, one per day."""
    import numpy as np
    from PIL import Image as PilImage, PngImagePlugin
    ims = sys.modules["img_scale"]
    cells = []
    for i in range(N):
        yy, xx = np.mgrid[0:120, 0:160]
        peak = 400 + 200 * i
        counts = np.exp(-(((xx - 80) ** 2 + (yy - 60) ** 2) / 900.0)) * peak
        bits = ims.bits_from_max_value(peak)
        stored = np.clip(np.rint(counts * ims.SCALE_FACTORS[bits]),
                         0, 65535).astype(np.uint16)
        ts = 1_700_000_000_000_000_000 + i * 1_000_000_000
        p = cam_dir / f"{CAM}_-_{ts}.png"
        meta = PngImagePlugin.PngInfo()
        meta.add_text("MaxValue", str(int(peak)))
        PilImage.fromarray(stored).save(p, pnginfo=meta)
        cells.append({"day": date(2026, 8, 10) + timedelta(days=i),
                      "cam": "C03-040-PTM11WNF", "cam_folder": CAM,
                      "path": p, "ts_ns": ts, "status": "found", "meta": {}})
    return cells


def key(w, name: str):
    from PySide6.QtCore import Qt, QEvent
    from PySide6.QtGui import QKeyEvent
    k = {"left": Qt.Key.Key_Left, "right": Qt.Key.Key_Right}[name]
    w.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k,
                              Qt.KeyboardModifier.NoModifier))


def main() -> int:
    _if = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="if_detail_"))
    cam_dir = tmp / CAM
    cam_dir.mkdir(parents=True)
    cells = write_frames(cam_dir)

    w = _if.ImageFinderWidget()
    w.resize(1200, 850)
    wall = w._wall
    wall.resize(1000, 700)
    wall.set_canvas(1000, 700)
    wall.set_cells(cells)
    end = time.monotonic() + 20
    while len(wall._raw) < len(cells) and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)

    print("\n=== the tab is there, and it is last ===")
    titles = [w._view_tabs.tabText(i) for i in range(w._view_tabs.count())]
    check("a Detailed view tab exists",
          any(t.startswith("Detailed view") for t in titles), repr(titles))
    check("and it is the last one", titles[-1].startswith("Detailed view"),
          repr(titles))
    check("it is not registered as a wall",
          w._detail_tab_idx not in w._wall_pages,
          f"tab {w._detail_tab_idx}, walls {sorted(w._wall_pages)}")

    print("\n=== nothing marked means the whole wall ===")
    w._on_wall_selection_changed()
    paths = [c["path"] for c in cells]
    check("every frame is in reach", w._preview_paths == paths,
          f"{len(w._preview_paths)} of {len(paths)}")
    check("the tab title counts them",
          "(1/5)" in w._view_tabs.tabText(w._detail_tab_idx),
          repr(w._view_tabs.tabText(w._detail_tab_idx)))

    print("\n=== marking tiles narrows it to those tiles ===")
    wall.set_selected(paths[3], True)
    wall.set_selected(paths[1], True)
    wall.set_selected(paths[4], True)
    check("three marked frames, in the wall's own order",
          w._preview_paths == [paths[1], paths[3], paths[4]],
          repr([p.name for p in w._preview_paths]))
    check("and the title counts three",
          "/3)" in w._view_tabs.tabText(w._detail_tab_idx),
          repr(w._view_tabs.tabText(w._detail_tab_idx)))

    print("\n=== the frame being looked at is kept ===")
    w._preview_next()          # -> paths[3], the second of the three
    was = w._preview_paths[w._preview_idx]
    wall.set_selected(paths[0], True)
    check("marking a fourth tile does not throw you back to the start",
          w._preview_paths[w._preview_idx] == was,
          f"{w._preview_paths[w._preview_idx].name} vs {was.name}")
    check("and the fourth is in reach now", len(w._preview_paths) == 4,
          repr([p.name for p in w._preview_paths]))

    print("\n=== the arrows wrap ===")
    wall.clear_selection()
    w._on_wall_selection_changed()
    w._preview_set_files(paths, "", [c["cam"] for c in cells], index=0)
    w._preview_prev()
    check("back from the first lands on the last",
          w._preview_idx == len(paths) - 1, str(w._preview_idx))
    w._preview_next()
    check("and forward from the last comes round again", w._preview_idx == 0,
          str(w._preview_idx))

    print("\n=== the keyboard steps it, but only on that tab ===")
    w._view_tabs.setCurrentIndex(w._detail_tab_idx)
    app.processEvents()
    check("the Detailed view is the tab on screen", w._detail_tab_is_current())
    key(w, "right")
    check("right steps one on", w._preview_idx == 1, str(w._preview_idx))
    key(w, "left")
    check("left steps one back", w._preview_idx == 0, str(w._preview_idx))
    wall_tab = min(w._wall_pages)
    w._view_tabs.setCurrentIndex(wall_tab)
    app.processEvents()
    key(w, "right")
    check("on a wall the arrows are left to the wall", w._preview_idx == 0,
          str(w._preview_idx))

    print("\n=== the walls still own the display controls ===")
    check("self._wall is a wall, not the Detailed view",
          isinstance(w._wall, _if._DayWall))
    w._view_tabs.setCurrentIndex(w._detail_tab_idx)
    app.processEvents()
    check("and it stays one when the Detailed view is on screen",
          w._wall is wall, repr(type(w._wall).__name__))
    check("Save view is greyed out there — one frame is not a view",
          not w._btn_save_wall.isEnabled())

    print("\n=== the picture actually reaches the page ===")
    end = time.monotonic() + 10
    while w._detail_view["img"].pixmap().isNull() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    pm = w._detail_view["img"].pixmap()
    check("the Detailed view is showing a frame",
          not pm.isNull() and pm.width() > 100, f"{pm.width()}x{pm.height()}")
    check("it names the camera",
          "PTM11WNF" in w._detail_view["cam"].text(),
          repr(w._detail_view["cam"].text()))
    check("and the time", ":" in w._detail_view["ts"].text(),
          repr(w._detail_view["ts"].text()))
    check("the close-up window's page is in step",
          w._frame_view["counter"].text() == w._detail_view["counter"].text(),
          f"{w._frame_view['counter'].text()} vs {w._detail_view['counter'].text()}")

    print("\n=== a search rebuilds the walls and keeps this page ===")
    results = {CAM: [(c["day"], 8, c["path"], {"sbw4": 1.0, "source": "sbw4"},
                      "found") for c in cells]}
    w.fill_wall(results, None)
    for _ in range(60):
        app.processEvents()
        time.sleep(0.01)
    titles = [w._view_tabs.tabText(i) for i in range(w._view_tabs.count())]
    check("the Detailed view survived the rebuild",
          titles[-1].startswith("Detailed view"), repr(titles))
    check("its widgets are still alive",
          w._detail_view["counter"].text() != "",
          repr(w._detail_view["counter"].text()))
    check("and it is still not a wall",
          w._detail_tab_idx not in w._wall_pages,
          f"tab {w._detail_tab_idx}, walls {sorted(w._wall_pages)}")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
