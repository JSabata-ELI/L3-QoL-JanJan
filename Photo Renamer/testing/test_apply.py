"""
test_apply.py  --  what Save and Recover actually do to the folder.

Covers the cases that are easy to get wrong: swapping two names, a clash
blocking Save, a name that Windows will not take, reverting one pending change
while the others survive, the grid never holding more than six in a row, and a
locked file coming back as a failure instead of disappearing quietly.

    python test_apply.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication          # noqa: E402

import pr_t                                          # noqa: E402
from make_sample_folder import build                 # noqa: E402

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


def test_swap_and_delete(folder: Path) -> None:
    print("swap two names, cross one out, apply")
    w = _widget(folder)
    was_01 = (folder / "shot_01.png").read_bytes()
    was_02 = (folder / "shot_02.png").read_bytes()
    a, b = _row_of(w, "shot_01.png"), _row_of(w, "shot_02.png")
    w.rename_item(a, "shot_02")
    w.rename_item(b, "shot_01")
    check(all(w._items[r].conflict == "" for r in (a, b)),
          "a straight swap is not reported as a clash")

    gone = _row_of(w, "shot_03.png")
    w.toggle_delete([gone])
    check(w._btn_save.isEnabled(), "Save is offered")

    renames = [it for it in w._items if it.renamed and not it.deleted]
    deletes = [it for it in w._items if it.deleted]
    results = w._apply(renames, deletes)
    errs = [f"{cells[2]}: {err}" for cells, err in results if err]
    check(not errs, "every operation reported Done" + (f" -- {errs}" if errs
                                                       else ""))

    # Both names must still be there, and each must now hold the OTHER picture.
    # A one-pass rename would have overwritten one of them instead.
    check((folder / "shot_01.png").exists() and (folder / "shot_02.png").exists(),
          "both swapped names exist")
    check((folder / "shot_01.png").read_bytes() == was_02
          and (folder / "shot_02.png").read_bytes() == was_01,
          "the two pictures really changed places")
    check(not (folder / "shot_03.png").exists(), "the crossed-out file is gone")
    check(not list(folder.glob(".__pr_*")), "no temporary files left behind")
    w.shutdown()


def test_save_without_asking(folder: Path) -> None:
    print("Save without asking goes straight through")
    w = _widget(folder)
    w._chk_quick.setChecked(True)
    r = _row_of(w, "shot_04.jpg")
    d = _row_of(w, "shot_05.png")
    w.rename_item(r, "quick_name")
    w.toggle_delete([d])
    # No dialog is shown, so this returns on its own; a dialog would block here.
    w._save()
    check((folder / "quick_name.jpg").exists(), "the rename happened")
    check(not (folder / "shot_05.png").exists(), "the deletion happened")
    check(not w.has_pending_changes(), "nothing is pending afterwards")
    check(not w._btn_recover.isEnabled(), "Recover is greyed out afterwards")
    w.shutdown()


def test_clash_blocks_save(folder: Path) -> None:
    print("a clash blocks Save")
    w = _widget(folder)
    a = _row_of(w, "shot_05.png")
    w.rename_item(a, "shot_06")
    check(w._items[a].conflict != "", "the clashing name is marked")
    check(not w._btn_save.isEnabled(), "Save is refused")

    # A name already held by a file we do not manage.
    b = _row_of(w, "shot_07.png")
    w.rename_item(b, "notes")
    w._items[b].suffix = ".txt"          # force the exact name of the text file
    w._recompute()
    check(w._items[b].conflict != "", "a name held by another file is marked")

    w.rename_item(a, "shot_05")
    w.rename_item(b, "shot_07")
    w._items[b].suffix = ".png"
    w._recompute()
    check(not any(it.conflict for it in w._items), "clearing the names clears it")
    w.shutdown()


def test_bad_characters(folder: Path) -> None:
    print("names Windows will not take")
    w = _widget(folder)
    r = _row_of(w, "shot_09.png")
    for bad in ("", "a/b", "a?b", "trailing."):
        w.rename_item(r, bad)
        check(w._items[r].conflict != "", f"refused: {bad!r}")
    # Spaces at the ends are quietly trimmed rather than refused -- a name
    # Windows would reject is not worth an error message when the fix is obvious.
    w.rename_item(r, "  padded  ")
    check(w._items[r].stem == "padded" and w._items[r].conflict == "",
          "spaces at the ends are trimmed away")
    w.rename_item(r, "shot_09")
    check(w._items[r].conflict == "", "a good name is accepted again")
    w.shutdown()


def test_partial_revert(folder: Path) -> None:
    print("revert one change, keep the rest")
    w = _widget(folder)
    r1 = _row_of(w, "shot_10.png")
    r2 = _row_of(w, "shot_11.png")
    r3 = _row_of(w, "shot_12.jpg")
    w.rename_item(r1, "keep_this")
    w.rename_item(r2, "drop_this")
    w.toggle_delete([r3])

    # What the Recover dialog hands back: only the middle row ticked.
    w._items[r2].stem = w._items[r2].orig_stem
    w._recompute()
    check(w._items[r1].new_name == "keep_this.png", "the other rename survives")
    check(not w._items[r2].renamed, "the reverted rename is gone")
    check(w._items[r3].deleted, "the deletion survives")
    check(w._btn_recover.isEnabled(), "Recover is still offered")
    w.shutdown()


def test_locked_file_is_reported(folder: Path) -> None:
    print("a file another program holds open comes back as a failure")
    w = _widget(folder)
    r = _row_of(w, "shot_06.png")
    w.rename_item(r, "will_not_work")
    it = w._items[r]
    # Windows refuses to rename a file that is open for reading, which is
    # exactly what an image viewer does to it.
    with open(it.path, "rb"):
        results = w._apply([it], [])
    fails = [(cells, err) for cells, err in results if err]
    check(len(fails) == 1, "the failure is reported, not swallowed")
    check(bool(fails and fails[0][1]), "it comes with a reason to show")
    check((folder / "shot_06.png").exists(),
          "the file is still there under its old name")
    check(not list(folder.glob(".__pr_*")), "no temporary file left behind")
    w.shutdown()


def test_grid_columns() -> None:
    print("the grid never puts more than six in a row")
    w = pr_t.PhotoRenamerWidget()
    check(w.columns_for(640) == 6, "a half-window pane gives six")
    check(w.columns_for(2000) == 6, "a very wide pane still gives six")
    check(w.columns_for(430) == 4, "a narrower pane gives four")
    check(w.columns_for(150) == 1, "a very narrow pane gives one")
    check(w.columns_for(0) == 1, "no width still gives one, never zero")
    w.shutdown()


def main() -> int:
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="pr_test_"))
    try:
        for fn in (test_swap_and_delete, test_save_without_asking,
                   test_clash_blocks_save, test_bad_characters,
                   test_partial_revert, test_locked_file_is_reported):
            folder = tmp / fn.__name__
            build(folder)
            fn(folder)
        test_grid_columns()
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
