"""
test_edit_save.py  --  what Save does to an edited picture.

Covers: the untouched original lands in "Original photos" byte for byte, the
picture in the folder is replaced by the edited one (turned and cropped to the
right size, the circle really drawn), an edited picture that is also renamed,
a second edit in a later session never overwriting the first original, the
originals folder staying out of the grid, the preview matching the file, and
Recover throwing an edit away.

    python test_edit_save.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

# No window is ever shown here; nothing needs a real screen.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage                # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

import pr_t                                              # noqa: E402
from pr_t import EditOp                                  # noqa: E402
from make_sample_folder import build                     # noqa: E402

_FAILS: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        _FAILS.append(what)


def _widget(folder: Path) -> pr_t.PhotoRenamerWidget:
    pr_t.load_ui_state = lambda: {}
    pr_t.save_ui_state = lambda state: None
    pr_t._remember = lambda key, value: None
    w = pr_t.PhotoRenamerWidget()
    w.open_folder(folder)
    return w


def _row_of(w: pr_t.PhotoRenamerWidget, name: str) -> int:
    for i, it in enumerate(w._items):
        if it.orig_name == name:
            return i
    raise AssertionError(f"{name} is not in the list")


def _edit_row(w: pr_t.PhotoRenamerWidget, row: int, ops: list[EditOp]) -> None:
    """Edit a row the way the panel does: show it, then add the steps."""
    w._preview_path = w._items[row].path
    for op in ops:
        w.add_edit(op)


def _is_red(c: QColor) -> bool:
    return c.red() > 180 and c.green() < 90 and c.blue() < 90


def test_edit_keeps_original(folder: Path) -> None:
    print("edit: circle, turn right, crop - then Save")
    w = _widget(folder)
    name = "shot_01.png"                     # 800 x 300 in the sample folder
    was = (folder / name).read_bytes()
    r = _row_of(w, name)
    _edit_row(w, r, [
        EditOp(kind="ellipse", pts=[(100.0, 50.0), (300.0, 250.0)],
               colour="#ff0000", width=12.0),
        EditOp(kind="rotate", turn=1),       # now 300 x 800
        EditOp(kind="crop", pts=[(0.0, 0.0), (300.0, 500.0)]),
    ])
    check(w._items[r].edited, "the picture counts as edited")
    check(w.has_pending_changes(), "an edit is a pending change")
    check(w._btn_save.isEnabled() and w._btn_recover.isEnabled(),
          "Save and Recover are offered")
    check(w._st_edit.text() == "1 edited", "the counter says 1 edited")

    edits = [it for it in w._items if it.edited]
    results = w._apply([], [], edits)
    errs = [err for _cells, err in results if err]
    check(not errs, "Save reported no failure" + (f" -- {errs}" if errs else ""))

    orig = folder / pr_t.ORIGINALS_DIR / name
    check(orig.exists() and orig.read_bytes() == was,
          "the original is in Original photos, byte for byte")
    img = QImage(str(folder / name))
    check((img.width(), img.height()) == (300, 500),
          f"turned and cropped to 300 x 500 (got {img.width()} x {img.height()})")
    # The circle's left edge was at x=100 on the 800-wide picture; after a turn
    # to the right that point (100, 150) sits at (300 - 150, 100) = (150, 100).
    check(_is_red(img.pixelColor(150, 100)),
          f"the circle is drawn where it was put (got {img.pixelColor(150, 100).name()})")
    check(not list(folder.glob(".__pr_*")), "no temporary file left behind")

    w._scan()
    check(all(pr_t.ORIGINALS_DIR not in str(it.path) for it in w._items),
          "Original photos is not listed in the grid")
    check(not w.has_pending_changes(), "nothing pending after the rescan")

    # A second edit in a later session: the first original must survive.
    r = _row_of(w, name)
    _edit_row(w, r, [EditOp(kind="rotate", turn=-1)])
    w._apply([], [], [w._items[r]])
    check(orig.read_bytes() == was, "the first original is never overwritten")
    second = folder / pr_t.ORIGINALS_DIR / "shot_01 (2).png"
    check(second.exists(), "the second original is kept as 'shot_01 (2).png'")
    w.shutdown()


def test_edit_and_rename_in_subfolder(folder: Path) -> None:
    print("edit and rename a picture in a sub-folder, through Save")
    w = _widget(folder)
    w._chk_quick.setChecked(True)
    r = _row_of(w, "beam_02.png")
    was = w._items[r].path.read_bytes()
    _edit_row(w, r, [EditOp(kind="rect", pts=[(10.0, 10.0), (200.0, 150.0)],
                            colour="#ff0000", width=8.0),
                     EditOp(kind="text", pts=[(20.0, 200.0)], colour="#ffd400",
                            width=40.0, text="look here")])
    w.rename_item(r, "beam_marked")
    w._save()
    orig = folder / pr_t.ORIGINALS_DIR / "day2" / "beam_02.png"
    check(orig.exists() and orig.read_bytes() == was,
          "the original is under Original photos/day2 with its old name")
    new = folder / "day2" / "beam_marked.png"
    check(new.exists() and not (folder / "day2" / "beam_02.png").exists(),
          "the edited picture carries the new name")
    img = QImage(str(new))
    check(_is_red(img.pixelColor(10, 80)), "the rectangle is in the saved file")
    w.shutdown()


def test_jpeg_and_preview_match(folder: Path) -> None:
    print("a JPEG is written back as JPEG; the preview is the same picture")
    w = _widget(folder)
    name = "shot_04.jpg"                     # 640 x 480
    r = _row_of(w, name)
    it = w._items[r]
    ops = [EditOp(kind="arrow", pts=[(50.0, 400.0), (400.0, 100.0)],
                  colour="#1e6fe0", width=10.0),
           EditOp(kind="pen", pts=[(10.0, 10.0), (60.0, 30.0), (120.0, 10.0)],
                  colour="#20a040", width=6.0),
           EditOp(kind="crop", pts=[(40.0, 0.0), (600.0, 460.0)])]
    full, fw, fh = pr_t.load_scaled(it.path, 0)
    small, _w, _h = pr_t.load_scaled(it.path, 320)
    big = pr_t.render_edits(full, fw, fh, ops)
    preview = pr_t.render_edits(small, fw, fh, ops)
    check((big.width(), big.height()) == pr_t.frame_after(fw, fh, ops),
          "frame_after gives the saved size")
    ratio_big = big.width() / big.height()
    ratio_prev = preview.width() / preview.height()
    check(abs(ratio_big - ratio_prev) < 0.02,
          "the preview has the same shape as the saved picture")

    _edit_row(w, r, ops)
    w._apply([], [], [it])
    data = (folder / name).read_bytes()
    check(data[:2] == b"\xff\xd8", "the file is still a JPEG")
    w.shutdown()


def test_recover_and_delete(folder: Path) -> None:
    print("Recover drops an edit; a crossed-out picture is never edited")
    w = _widget(folder)
    r = _row_of(w, "shot_05.png")
    _edit_row(w, r, [EditOp(kind="rotate", turn=1)])
    w.undo_edit()
    check(not w._items[r].edited, "Undo removes the last step")
    _edit_row(w, r, [EditOp(kind="rotate", turn=1),
                     EditOp(kind="rotate", turn=1)])
    w.reset_edits()
    check(not w._items[r].edited, "Reset removes every step")

    _edit_row(w, r, [EditOp(kind="rotate", turn=1)])
    was = (folder / "shot_05.png").read_bytes()
    w.toggle_delete([r])
    edits = [it for it in w._items if it.edited and not it.deleted]
    check(not edits, "a crossed-out picture is not in the edit list")
    w.toggle_delete([r])
    check(w._items[r].edited, "putting it back keeps its edit")
    w._items[r].edits.clear()
    w._recompute()
    check(not w.has_pending_changes(), "no edit left, nothing pending")
    check((folder / "shot_05.png").read_bytes() == was, "the file is untouched")
    check(not (folder / pr_t.ORIGINALS_DIR).exists(),
          "no Original photos folder without a saved edit")
    w.shutdown()


def test_bigger_tiles() -> None:
    print("two columns always, three from 600 px")
    check(pr_t.MIN_TILE_W == 200, "three columns need 200 px each")
    check((pr_t.MIN_COLUMNS, pr_t.MAX_COLUMNS) == (2, 3), "two to three in a row")


def main() -> int:
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="pr_edit_test_"))
    try:
        for fn in (test_edit_keeps_original, test_edit_and_rename_in_subfolder,
                   test_jpeg_and_preview_match, test_recover_and_delete):
            folder = tmp / fn.__name__
            build(folder)
            fn(folder)
        test_bigger_tiles()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        del app
    print()
    if _FAILS:
        print(f"{len(_FAILS)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
