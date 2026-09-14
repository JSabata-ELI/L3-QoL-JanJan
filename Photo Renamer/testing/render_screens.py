"""
render_screens.py  --  screenshots of every state, for the legibility check.

Run with the real Windows platform plugin: the offscreen one has no fonts, draws
boxes instead of letters and lies about text size.

    set QT_QPA_PLATFORM=windows
    python render_screens.py <sample folder> <output folder>

Writes: main.png, hover.png, editing.png, subfolder.png, marked.png, clash.png,
save_preview.png, recover.png, smallest.png, menu.png
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, QTimer                        # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu                # noqa: E402

import pr_t                                                      # noqa: E402
import main as main_mod                                          # noqa: E402
from main import PhotoRenamerWindow                              # noqa: E402


def _settle(times: int = 40) -> None:
    app = QApplication.instance()
    for _ in range(times):
        app.processEvents()


def _grab(widget, out: Path) -> None:
    _settle(8)
    widget.grab().save(str(out), "PNG")
    print(f"  {out.name}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    sample = Path(sys.argv[1])
    outdir = Path(sys.argv[2])
    outdir.mkdir(parents=True, exist_ok=True)

    # Do not let the run pick up, or write, the user's own remembered settings.
    pr_t.load_ui_state = lambda: {}
    pr_t.save_ui_state = lambda state: None
    pr_t._remember = lambda key, value: None
    main_mod.load_ui_state = lambda: {}
    main_mod.save_ui_state = lambda state: None

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget      { background: #f3f3f3; color: #111; }
        QLabel       { background: transparent; }
        QPushButton  { padding: 5px 8px; }
        QToolTip { background: #ffffcc; color: #111;
                   border: 1px solid #aaa; padding: 4px; }
    """)

    win = PhotoRenamerWindow()
    win.resize(1320, 860)
    win.show()
    w = win.centralWidget()
    w.open_folder(sample)
    _settle()

    lst = w._list
    print(f"  (tile {w.tile_size().width()}x{w.tile_size().height()}, "
          f"{w.columns_for(w.grid_width())} per row)")

    # 1 -- the plain grid, with the third picture selected so the preview panel
    #      has something in it.
    lst.setCurrentRow(2)
    w._show_row(2)
    _settle()
    _grab(win, outdir / "main.png")

    # 2 -- mouse over another picture: the preview panel follows the mouse.
    w._hover_row = 5
    w._show_row(5)
    w._load_hover_preview()
    _settle()
    _grab(win, outdir / "hover.png")

    # 3 -- the rename editor open on a tile.
    lst.setCurrentRow(1)
    lst.edit(lst.model().index(1, 0))
    _settle(10)
    _grab(win, outdir / "editing.png")
    lst.closePersistentEditor(lst.item(1))
    _settle(5)

    # 4 -- scrolled to the sub-folder pictures.
    lst.scrollToBottom()
    _settle(30)
    _grab(win, outdir / "subfolder.png")
    lst.scrollToTop()
    _settle(10)

    # 5 -- two crossed out, two renamed.
    w.toggle_delete([3, 7])
    w.rename_item(0, "first_shot")
    w.rename_item(4, "renamed_beam")
    lst.setCurrentRow(3)
    w._show_row(3)
    _settle()
    _grab(win, outdir / "marked.png")

    # 6 -- a clash: give one picture the name another one already has.
    other = w.item_at(6)
    w.rename_item(5, other.stem)
    lst.setCurrentRow(5)
    w._show_row(5)
    _settle(30)
    _grab(win, outdir / "clash.png")
    w.rename_item(5, "back_to_unique")
    _settle(10)

    # 7 -- the right-click menu, built the same way the list builds it.
    item = w.item_at(0)
    menu = QMenu(win)
    menu.setStyleSheet(pr_t._MENU_QSS)
    menu.addAction("Open picture")
    menu.addAction("Show in Explorer")
    menu.addSeparator()
    menu.addAction("Rename")
    menu.addAction("Put the file back" if item.deleted
                   else "Mark the file for deletion")
    menu.popup(win.mapToGlobal(QPoint(80, 120)))
    _settle(20)
    _grab(menu, outdir / "menu.png")
    menu.close()
    _settle(5)

    # 8 -- the Save preview table, which is what an unticked "Save without
    #      asking" shows.
    renames = [it for it in w._items if it.renamed and not it.deleted]
    deletes = [it for it in w._items if it.deleted]
    rows = [(["rename", it.rel_dir, it.orig_name, it.new_name], "")
            for it in renames]
    rows += [(["delete", it.rel_dir, it.orig_name, ""], "delete")
             for it in deletes]
    dlg = pr_t._SavePreviewDialog(rows, len(renames), len(deletes), win)
    dlg.show()
    _settle(20)
    _grab(dlg, outdir / "save_preview.png")
    dlg.reject()

    # 9 -- the Recover dialog.
    changes = []
    for row, it in enumerate(w._items):
        if it.deleted:
            changes.append((row, "delete", f"delete:  {it.orig_name}"))
        elif it.renamed:
            changes.append((row, "rename",
                            f"rename:  {it.orig_name}   →   {it.new_name}"))
    rec = pr_t._RecoverDialog(changes, win)
    rec.show()
    _settle(20)
    _grab(rec, outdir / "recover.png")
    rec.reject()

    # 10 -- the window at the smallest size it allows. Every button in the top
    #       bar has to still be there; that is the whole point of the shot.
    win.resize(win.minimumSizeHint().width(), win.minimumHeight())
    _settle(30)
    print(f"  (smallest window {win.width()}x{win.height()}, "
          f"{w.columns_for(w.grid_width())} per row)")
    _grab(win, outdir / "smallest.png")

    # No app.exec() and no win.close() here. Everything above ran on
    # processEvents; entering the event loop only to quit it hung the script (the
    # popup menu keeps a grab), and closing the window asks the user about the
    # pending changes with a modal box that nobody is there to answer.
    w._stop_loading()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
