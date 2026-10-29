"""The Workshop's collage: several images in one grid.

Checked, each one a thing the operator relies on:

  * **The grid.** Four images of different sizes come out 2 × 2 with Auto columns,
    the output size follows the gap and the column count, and "Same size" fits every
    picture into one common tile.
  * **The Images table.** It holds every open image; one Show tick = the single
    view, two or more = the grid; the last tick cannot be taken away; a click on a
    row with one picture shown moves the tick there; ▲ / ▼ change the order in the
    grid; Ref is one image at a time.
  * **The drawing.** With "Drawings and measurements" ticked, a drawn item is in the
    collage; unticked, it is not.
  * **A view, not an edit.** While the grid is shown editing is refused, and "Make a
    new image" adds the collage as an image of its own, shown alone.
  * **PV values.** A frame whose file name carries its moment gets a PV line; a file
    opened from the computer gets none at all. The moment survives a saved session.
    The archive is not touched — the PV lookup is replaced by a stand-in.

The window is never shown (no focus is taken); widgets are rendered with grab().
Rendering uses the real windows platform, since offscreen has no fonts.

Not shipped: the builder keeps test_* out of the bundle.

Usage:

    python testing/test_workshop_collage.py
    python testing/test_workshop_collage.py --shot out.png   # save screenshots

Exit code is 1 when a claim fails.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# A scratch APPDATA, so the test never touches the real Workshop settings.
_TMP = Path(tempfile.mkdtemp(prefix="eli_wk_collage_test_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                              # noqa: E402
from PIL import Image as _PilImg                                # noqa: E402
import wk_t                                                     # noqa: E402
from PySide6.QtGui import QImage                                # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog         # noqa: E402


FAILURES: list = []


def check(ok: bool, what: str, detail: str = ""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  -- " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)


def _pump(seconds: float = 0.0):
    end = time.monotonic() + seconds
    while True:
        QApplication.processEvents()
        if time.monotonic() >= end:
            break
        time.sleep(0.01)


def _img(w, h, level):
    a = np.zeros((h, w, 3), np.uint8)
    a[:, :] = level
    return a


# ── 1. The pure layout ─────────────────────────────────────────────────────
def test_layout():
    print("The grid")
    check(wk_t.collage_grid(4) == (2, 2), "4 images, Auto: 2 × 2")
    check(wk_t.collage_grid(5) == (3, 2), "5 images, Auto: 3 × 2")
    check(wk_t.collage_grid(2) == (2, 1), "2 images, Auto: one row")
    check(wk_t.collage_grid(4, 4) == (4, 1), "4 images, 4 columns: one row")
    check(wk_t.collage_grid(3, 8) == (3, 1), "more columns than images: no empty column")

    tiles = [{"image": wk_t._np_to_qimage(_img(100, 50, 60)), "label": "", "pv": None}
             for _ in range(4)]
    opts = {"columns": 0, "tiles": "1:1 pixels", "gap": 10, "label": "Off"}
    out = wk_t.render_collage(tiles, opts)
    check(out is not None and (out.width(), out.height()) == (230, 130),
          "2 × 2 of 100 × 50 with a 10 px gap is 230 × 130",
          f"{out.width()} × {out.height()}" if out else "none")

    opts["background"] = "White"
    out = wk_t.render_collage(tiles, opts)
    check(out.pixelColor(2, 2).name() == "#ffffff" and
          out.pixelColor(50, 30).name() == "#3c3c3c",
          "the gap takes the ground colour, the picture stays itself")

    mixed = [{"image": wk_t._np_to_qimage(_img(200, 100, 60)), "label": "", "pv": None},
             {"image": wk_t._np_to_qimage(_img(50, 25, 200)), "label": "", "pv": None}]
    out = wk_t.render_collage(mixed, {"tiles": "Same size", "gap": 0, "label": "Off"})
    check(out.width() == 400 and out.pixelColor(390, 90).name() == "#c8c8c8",
          "Same size blows the small image up to the big one's tile",
          f"{out.width()} × {out.height()}")

    named = [{"image": wk_t._np_to_qimage(_img(300, 100, 60)),
              "label": "a rather long camera name 2026-10-27 12:00:00.000", "pv": None}]
    plain = wk_t.render_collage(named, {"gap": 0, "label": "Off"})
    lab = wk_t.render_collage(named, {"gap": 0, "label": "Name"})
    check(lab.height() > plain.height(), "a name adds a strip under the picture")


# ── 2. The panel ───────────────────────────────────────────────────────────
def _panel():
    w = wk_t.WorkshopWidget()
    w.resize(1400, 900)
    # Never shown: grab() renders it without a window, so no focus is taken.
    return w


def test_panel(panel, shot):
    print("The collage in the Workshop")
    sizes = [(120, 80), (60, 40), (200, 100), (80, 80)]
    for i, (w, h) in enumerate(sizes):
        panel.receive_image(_img(w, h, 40 + 50 * i), f"img{i + 1}")
    _pump()
    from PySide6.QtCore import Qt
    t = panel._img_table
    show = panel._C_SHOW

    def ticks():
        return [t.item(r, show).checkState() == Qt.CheckState.Checked
                for r in range(t.rowCount())]

    check(t.rowCount() == 4, "the table holds every open image")
    check(ticks() == [False, False, False, True] and not panel._canvas.comparing(),
          "one image shown — the newest, on its own", f"{ticks()}")

    # A click on another row with one picture shown moves the tick there.
    t.setCurrentCell(1, panel._C_NAME)
    _pump()
    check(panel._active == 1 and ticks() == [False, True, False, False],
          "a row click with one picture shown: that one is shown", f"{ticks()}")
    # The last tick stays.
    t.item(1, show).setCheckState(Qt.CheckState.Unchecked)
    _pump()
    check(ticks() == [False, True, False, False], "the last Show tick cannot be taken")

    # Ref: one at a time.
    t.item(0, panel._C_REF).setCheckState(Qt.CheckState.Checked)
    t.item(2, panel._C_REF).setCheckState(Qt.CheckState.Checked)
    _pump()
    refs = [t.item(r, panel._C_REF).checkState() == Qt.CheckState.Checked
            for r in range(4)]
    check(refs == [False, False, True, False] and panel._ref == 2,
          "Ref is one image at a time", f"{refs}")

    panel._collage_gap.setValue(0)
    panel._collage_tiles.setCurrentText("1:1 pixels")
    panel._collage_label.setCurrentText("Off")
    panel._collage_tick_all(True)
    _pump()
    check(panel._canvas.comparing(), "Show all: the grid is on the canvas")
    img = panel._canvas.display_image()
    # Columns: max(120, 200) + max(60, 80); rows: max(80, 40) + max(100, 80).
    check(img is not None and (img.width(), img.height()) == (280, 180),
          "2 × 2 of the four sizes, 1:1, no gap", f"{img.width()} × {img.height()}")

    # Order: move image 1 down → image 2 is now top-left.
    t.setCurrentCell(0, panel._C_NAME)
    _pump()
    check(panel._active == 0 and panel._canvas.comparing(),
          "with the grid shown, a row click only picks the active image")
    panel._collage_move(1)
    _pump()
    check(panel._collage_order[0].label == "img2", "▼ moves the marked image later")
    check(t.currentRow() == 1, "the marked row moves with its image")
    img = panel._canvas.display_image()
    # img2 (60 × 40) now sits centred in the first cell (200 wide, 80 tall).
    check(img.pixelColor(100, 40).name() == "#5a5a5a", "the grid follows the new order")

    # Untick one: three tiles left.
    t.item(3, show).setCheckState(Qt.CheckState.Unchecked)
    _pump()
    check(len(panel._collage_slots()) == 3, "an unticked image leaves the grid")
    t.item(3, show).setCheckState(Qt.CheckState.Checked)
    _pump()

    # Editing is refused.
    check(panel._editing_blocked(), "editing is refused while the collage shows")

    # Drawing in and out.
    slot = panel._collage_order[0]
    a = wk_t._Annot(wk_t.A_RULER, [[2.0, 2.0], [50.0, 30.0]], "#ff0000", 3)
    slot.annots.append(a)
    panel._cb_collage_draw.setChecked(False)
    _pump()
    off = panel._canvas.display_image().copy()
    panel._cb_collage_draw.setChecked(True)
    _pump()
    on = panel._canvas.display_image().copy()
    check(on != off, "Drawings and measurements puts the drawing in the collage")

    # Save writes the collage.
    saved = panel._render_for_save(panel._slot())
    check(saved is not None and saved.size() == on.size(), "Save / Copy take the collage")

    if shot is not None:
        panel._sections["view"].set_expanded(True)
        _pump()
        panel.grab().save(str(shot))
        # The Images table, scrolled into view — one row unticked, so both tick
        # states are in the picture.
        t.item(2, show).setCheckState(Qt.CheckState.Unchecked)
        panel._panel_scroll.ensureWidgetVisible(t, 0, 0)
        _pump()
        panel._panel_scroll.grab().save(str(shot.with_name(shot.stem + "_panel.png")))
        panel._panel_scroll.ensureWidgetVisible(panel._btn_collage_new, 0, 0)
        _pump()
        panel._panel_scroll.grab().save(str(shot.with_name(shot.stem + "_display.png")))

    # Make a new image.
    n = len(panel._slots)
    panel._collage_to_slot()
    _pump()
    check(len(panel._slots) == n + 1, "Make a new image adds one image")
    new = panel._slots[-1]
    check(panel._collage_slots() == [new] and not panel._canvas.comparing(),
          "after it, the new image is shown on its own")
    slot.annots.clear()


def test_pvs(panel):
    print("PV values per tile")
    panel._slots.clear()
    panel._update_slot_list()
    arch = _TMP / "cam"
    arch.mkdir(exist_ok=True)
    ts = 1_761_562_800_123_000_000
    p_arch = arch / f"cam_{ts}.png"
    _PilImg.fromarray(_img(60, 40, 90)).save(p_arch)
    p_home = arch / "my_picture.png"
    _PilImg.fromarray(_img(60, 40, 150)).save(p_home)

    panel.receive_image(_img(60, 40, 90), "from the Finder", source_path=p_arch)
    panel.open_files([str(p_home)])
    _pump()
    check(panel._slots[0].ts_ns == ts, "a frame from the archive knows its moment")
    check(panel._slots[1].ts_ns is None, "a file from the computer has no moment")

    sl = wk_t._get_slider_module()
    calls = []
    real_text, real_warm = sl.pv_text_for_ts, sl.pv_warm_days
    sl.pv_text_for_ts = lambda t, names, *_a: (calls.append(t) or "Energy: 1.23 J")
    sl.pv_warm_days = lambda *_a, **_k: None
    try:
        panel._collage_opts["pvs"] = ["Energy"]
        panel._collage_opts["pv_hidden"] = []
        panel._cb_collage_pvs.setChecked(True)
        panel._collage_tick_all(True)
        _pump(1.0)
        tiles = panel._collage_tiles_now()
        check(calls == [ts], "the archive is asked once, for the archive frame only",
              f"{calls}")
        check(tiles[0]["pv"] == "Energy: 1.23 J", "the archive frame carries its PV line")
        check(tiles[1]["pv"] is None, "the computer file has no PV line at all")
        img_pv = panel._canvas.display_image()
        panel._cb_collage_pvs.setChecked(False)
        _pump()
        img_no = panel._canvas.display_image()
        check(img_pv.height() > img_no.height(), "the PV bar shows under the tile")
    finally:
        sl.pv_text_for_ts, sl.pv_warm_days = real_text, real_warm
        panel._collage_tick_all(False)

    # Session round trip keeps the moment.
    sess = _TMP / "s.json"
    real_save, real_open = QFileDialog.getSaveFileName, QFileDialog.getOpenFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(sess), ""))
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(sess), ""))
    try:
        panel._save_session()
        data = json.loads(sess.read_text(encoding="utf-8"))
        panel._slots.clear()
        panel._update_slot_list()
        panel._load_session()
        _pump()
    finally:
        QFileDialog.getSaveFileName, QFileDialog.getOpenFileName = real_save, real_open
    check([e.get("ts_ns") for e in data["images"]] == [ts, None],
          "the session writes the moment down")
    check([s.ts_ns for s in panel._slots] == [ts, None],
          "a loaded session gives the moment back, and no more")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", type=Path, default=None)
    args = ap.parse_args()
    app = QApplication.instance() or QApplication(sys.argv)
    test_layout()
    panel = _panel()
    test_panel(panel, args.shot)
    test_pvs(panel)
    _ = app
    print()
    print("ALL OK" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
