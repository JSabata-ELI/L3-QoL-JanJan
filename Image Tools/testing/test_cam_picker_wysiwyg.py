"""The camera picker: the picked list, the two columns, and "what you see is applied".

Five claims, each one a complaint from the floor:

  * **The picked list keeps its place.** Removing a camera rebuilds the list, and the
    rebuild used to take the scrollbar back to the top with it — so with twenty cameras
    picked you scrolled down again after every single ✕.

  * **Five cameras are visible.** The list was pinned to a height that showed three.

  * **The board is beside the lists, not under them.** Stacked, the height was a
    zero-sum fight: the board's stretch squeezed the list to three rows and a floor
    under the list took the pixels straight back off the board.

  * **A size press re-arranges, always.** It used to be skipped whenever the board was
    not already automatic — which is every camera set that had ever been dragged, since
    the board opens on a remembered arrangement when there is one. The button wrote the
    size to disk and changed nothing anybody could see.

  * **The arrangement is applied verbatim.** An automatic arrangement used to be handed
    over as "None = work it out yourself", and the live grid then re-ran the packer from
    different inputs (its own size, freshly built tiles with no picture in them, so
    name-hint aspects). The arrangement previewed was not the one that appeared.

  * **A click does not change the arrangement.** Selecting a tile brings it to the front,
    which permuted the list the order-sensitive packer is fed — so a size press looked
    like it took effect one camera late.

Rendering uses the real windows platform: offscreen has no fonts and lies about sizes.

Usage:

    python testing/test_cam_picker_wysiwyg.py
    python testing/test_cam_picker_wysiwyg.py --shot out.png

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

# A scratch APPDATA so the test never touches the real cam_sizes.json / cam_layouts.json.
_TMP = tempfile.mkdtemp(prefix="eli_picker_test_")
os.environ["APPDATA"] = _TMP

import is_t as sl                                    # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str, detail: str = ""):
    print(("  ok   " if ok else "  FAIL ") + what + (("  - " + detail) if detail else ""))
    if not ok:
        FAILURES.append(what)


def _app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }")
    return app


def _cams(n: int) -> list:
    return [f"C03-{100 + i:03d}-CAM{i:02d}FF-IMG" for i in range(n)]


def _picker(cams, picked=None, show=True):
    from datetime import date                          # noqa: PLC0415
    return sl.CameraPickerDialog(
        date.today(), 8, 19, picked if picked is not None else cams, None,
        preloaded_cameras=[(str(i + 1), c) for i, c in enumerate(cams)],
        multi_grid=None, windows=None, cam_area_px=(1600, 880),
        label_font_px=20, show_layout=show)


def _settle(app, n=8):
    for _ in range(n):
        app.processEvents()


# ── 1. The picked list keeps its scroll position ────────────────────────────
def test_scroll_kept(app):
    print("Removing a camera does not scroll the list back to the top")
    cams = _cams(20)
    dlg = _picker(cams)
    dlg.show()
    _settle(app)

    bar = dlg._sel_table.verticalScrollBar()
    check(bar.maximum() > 0, "twenty cameras really do overflow the list",
          f"scroll range 0..{bar.maximum()}")
    target = bar.maximum()
    bar.setValue(target)
    _settle(app)
    check(bar.value() == target, "scrolled to the bottom", f"at {bar.value()}")

    # Remove a camera from the middle — the ✕ button's handler.
    dlg._remove_selected(cams[10])
    _settle(app)
    after = bar.value()
    # 19 rows instead of 20, so the bottom moved up by one row at most.
    check(after >= min(target, bar.maximum()) - 2,
          "the list stayed where the user had scrolled it",
          f"was {target}, now {after}, max {bar.maximum()}")
    check(dlg._sel_table.rowCount() == 19, "the camera really was removed",
          f"{dlg._sel_table.rowCount()} rows")
    dlg.close()


# ── 2. Five rows visible, board beside the lists ────────────────────────────
def test_five_rows_and_columns(app):
    print("Five cameras fit, and the board sits beside the lists")
    cams = _cams(20)
    dlg = _picker(cams)
    dlg.show()
    _settle(app)

    t = dlg._sel_table
    row_h = t.verticalHeader().defaultSectionSize()
    head_h = t.horizontalHeader().height()
    body = t.viewport().height()
    visible = body // max(1, row_h)
    check(visible >= 5, "at least five camera rows are visible",
          f"{visible} rows of {row_h} px in a {body} px viewport "
          f"(header {head_h}, widget {t.height()})")

    # Every visible row must be a full row, not a sliver: the ✕ button is 24 px.
    check(row_h >= 24, "a row is tall enough for its remove button", f"{row_h} px")

    # The board is to the RIGHT of the list, not below it.
    lt = dlg._left_col.mapTo(dlg, dlg._left_col.rect().topLeft())
    bt = dlg._layout_box.mapTo(dlg, dlg._layout_box.rect().topLeft())
    check(bt.x() >= lt.x() + dlg._left_col.width() - 4,
          "the board starts to the right of the lists",
          f"lists end at x={lt.x() + dlg._left_col.width()}, board starts at x={bt.x()}")
    check(abs(bt.y() - lt.y()) < dlg._left_col.height() // 2,
          "the board is level with the lists, not under them",
          f"lists y={lt.y()}, board y={bt.y()}")
    check(dlg._layout_box.width() > 300,
          "the board got a usable width", f"{dlg._layout_box.width()} px")
    dlg.close()


# ── 3. A size press always re-arranges ──────────────────────────────────────
def test_size_press_rearranges(app):
    print("A size press re-arranges the board, hand-made arrangement or not")
    cams = _cams(4)
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    dlg = _picker(cams)
    dlg.show()
    _settle(app)

    # Force the board into the state a remembered hand-made arrangement leaves it in.
    dlg._board._auto_mode = False
    dlg._board._user_edited = True
    before = [list(t) for t in dlg._board._tiles]

    dlg._size_row.set_camera(cams[0])
    dlg._size_row._pick("largest")
    _settle(app)
    after = [list(t) for t in dlg._board._tiles]
    check(after != before,
          "the arrangement changed on the press, not on the next one")
    check(sl.cam_size_class(cams[0]) == "largest", "the size was stored")

    # The camera stays selected, so the size can be corrected without hunting for it.
    check(dlg._board._selected == dlg._board._cam_names.index(cams[0]),
          "the camera whose size was set is still selected")

    # And the change is in the right direction: that camera's tile got bigger.
    i = dlg._board._cam_names.index(cams[0])
    grew = after[i][2] * after[i][3] > before[i][2] * before[i][3]
    check(grew, "the camera set to Largest got a bigger tile",
          f"{before[i][2] * before[i][3]:.3f} -> {after[i][2] * after[i][3]:.3f}")
    dlg.close()


# ── 4. A plain click must not change the arrangement ────────────────────────
def test_click_does_not_rearrange(app):
    print("Selecting a camera does not move the others")
    cams = _cams(5)
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    dlg = _picker(cams)
    dlg.show()
    _settle(app)

    def tiles_by_name(board):
        return {nm: [round(v, 6) for v in t]
                for nm, t in zip(board._cam_names, board._tiles)}

    base = tiles_by_name(dlg._board)
    # Simulate the selection click's effect: it brings the tile to the front of the
    # draw order. Then re-arrange. The answer must not depend on which was clicked.
    for pick in (3, 1, 4):
        b = dlg._board
        b._tiles.append(b._tiles.pop(pick))
        b._cam_names.append(b._cam_names.pop(pick))
        b._aspects.append(b._aspects.pop(pick))
        b._reset_tiles()
    _settle(app)
    after = tiles_by_name(dlg._board)
    same = all(abs(a - c) < 1e-6
               for nm in base
               for a, c in zip(base[nm], after.get(nm, [9, 9, 9, 9])))
    check(same, "the arrangement is the same whatever was clicked",
          "" if same else f"{base} vs {after}")
    dlg.close()


# ── 5. What the board shows is what is applied ──────────────────────────────
def test_wysiwyg_on_accept(app):
    print("OK hands over exactly the arrangement on the board")
    cams = _cams(4)
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    sl.set_cam_size_class(cams[1], "largest")
    dlg = _picker(cams)
    dlg.show()
    _settle(app)

    # Click a tile first, so the board's own list is NOT in the picked order any more —
    # the entries handed over are keyed by camera name and must survive that.
    b = dlg._board
    b._tiles.append(b._tiles.pop(1))
    b._cam_names.append(b._cam_names.pop(1))
    b._aspects.append(b._aspects.pop(1))
    check(b._cam_names != dlg._selected_names,
          "the board's order really does differ from the picked order")

    board_tiles = {nm: [round(v, 6) for v in t]
                   for nm, t in zip(dlg._board._cam_names, dlg._board._tiles)}
    check(dlg._board_is_auto(), "the board is on the automatic arrangement")

    dlg._on_accept()
    cfg = dlg.layout_config
    check(cfg is not None,
          "an automatic arrangement is handed over as fractions, not as 'work it out'")
    if cfg is not None:
        # cfg.entries is in the PICKED order — that is the order setup_cameras builds
        # the tiles in, so this is where a name/position mix-up would show up.
        applied = {nm: [round(e.x, 6), round(e.y, 6), round(e.w, 6), round(e.h, 6)]
                   for nm, e in zip(dlg._selected_names, cfg.entries)}
        same = all(abs(a - b) < 1e-6
                   for nm in board_tiles
                   for a, b in zip(board_tiles[nm], applied.get(nm, [9, 9, 9, 9])))
        check(same, "every tile handed over is the tile that was drawn",
              "" if same else f"{board_tiles} vs {applied}")

    # A single camera still fills the area on its own — no frozen fractions there.
    dlg2 = _picker(cams, picked=[cams[0]])
    dlg2.show()
    _settle(app)
    dlg2._on_accept()
    check(dlg2.layout_config is None, "one camera is left to fill the area itself")
    dlg2.close()
    dlg.close()


# ── 6. End to end: the real grid lands where the board drew it ──────────────
def test_real_grid_matches_board(app):
    """The one that matters: take what OK hands over, give it to the REAL grid, and
    compare where the tiles ended up with where the board drew them. This is the path
    that used to diverge — the grid re-ran the packer with no pictures in the tiles."""
    print("The live grid lands exactly where the board drew it")
    from PySide6.QtWidgets import QWidget, QVBoxLayout    # noqa: PLC0415

    cams = ["C03-200-AAA1FF-IMG", "C03-201-PD3M1XDF-IMG",
            "C03-202-CCC1FF-IMG", "C03-203-DDD1FF-IMG"]
    for c in cams:
        sl.set_cam_size_class(c, "medium")
    sl.set_cam_size_class(cams[0], "largest")
    sl.set_cam_size_class(cams[3], "small")

    live_w, live_h = 1500, 820
    dlg = _picker(cams)
    dlg._cam_area_px = (live_w, live_h)
    dlg._refresh_board()
    dlg.show()
    _settle(app)
    # Bring a tile to the front first, the way a selection click does, so the board's
    # order differs from the picked order on the path being measured.
    b = dlg._board
    b._tiles.append(b._tiles.pop(2))
    b._cam_names.append(b._cam_names.pop(2))
    b._aspects.append(b._aspects.pop(2))

    board_of = {nm: list(t) for nm, t in zip(dlg._board._cam_names, dlg._board._tiles)}
    dlg._on_accept()
    cfg = dlg.layout_config
    check(cfg is not None, "OK produced an arrangement")
    if cfg is None:
        dlg.close()
        return

    host = QWidget()
    host.resize(live_w, live_h)
    hl = QVBoxLayout(host)
    hl.setContentsMargins(0, 0, 0, 0)
    grid = sl.MultiCameraGrid(host)
    hl.addWidget(grid)
    host.show()
    _settle(app)
    # Exactly what the tab does: the PICKED names plus the config OK handed over.
    grid.setup_cameras(dlg.selected_camera_names(), layout_config=cfg)
    _settle(app, 10)

    W, H = grid.width(), grid.height()
    worst = 0.0
    for cv in grid._cam_views:
        t = board_of[cv.cam_name]
        want = sl._entry_rect(t[0], t[1], t[2], t[3], W, H)
        got = cv.geometry()
        worst = max(worst,
                    abs(want.x() - got.x()), abs(want.y() - got.y()),
                    abs(want.width() - got.width()), abs(want.height() - got.height()))
    check(worst <= 2, "every tile sits where the board drew it",
          f"worst edge off by {worst:.0f} px in a {W}x{H} area")
    host.close()
    dlg.close()


# ── 7. The no-grid mode (Shot Finder) is unchanged ──────────────────────────
def test_no_grid_mode(app):
    print("The tabs with no camera grid still get the narrow, board-less window")
    cams = _cams(3)
    dlg = _picker(cams, show=False)
    dlg.show()
    _settle(app)
    check(not dlg._layout_box.isVisible(), "no Layout section where there is no grid")
    check(dlg.width() <= 700, "the window is still the narrow one", f"{dlg.width()} px")
    dlg._on_accept()
    check(dlg.layout_config is None, "the no-grid mode returns no arrangement")
    dlg.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shot", default="")
    args = ap.parse_args()

    app = _app()
    test_scroll_kept(app)
    test_five_rows_and_columns(app)
    test_size_press_rearranges(app)
    test_click_does_not_rearrange(app)
    test_wysiwyg_on_accept(app)
    test_real_grid_matches_board(app)
    test_no_grid_mode(app)

    if args.shot:
        dlg = _picker(_cams(20))
        dlg.show()
        _settle(app, 12)
        dlg.grab().save(args.shot)
        print("screenshot:", args.shot)
        dlg.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + "; ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
