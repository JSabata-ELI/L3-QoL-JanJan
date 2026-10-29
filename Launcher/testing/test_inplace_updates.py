"""Changes update the window in place; nothing pops up by itself.

Run from the Launcher folder:  python testing/test_inplace_updates.py

Puts nothing on screen (QT_QPA_PLATFORM=minimal:enable_fonts, real fonts).
Pins what the Tk Launcher's test pinned: acknowledging an update, the version
poll, moving a program and opening a group change the cards in place instead
of building the window again; the version list is read when it opens; no
tidy-up dialog opens after a scan. Plus: names are never cut, and the window
is wide enough for two cards.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox  # noqa: E402
from shiboken6 import isValid  # noqa: E402

import l  # noqa: E402

TMP = Path(tempfile.mkdtemp())
l.CONFIG_PATH = TMP / "config.json"          # never touch the user's settings
l.Launcher._auto_select_root = lambda self: None
l.Launcher._schedule_version_poll = lambda self: None


def touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")


def make_programs() -> dict:
    progs = {}
    for name in ("Image Tools", "Screenshots", "Foo", "A Very Long Program Name Indeed"):
        d = TMP / "root" / name
        exe = d / f"{name} v1.0.0.exe"
        touch(exe)
        touch(d / f"ReadMe_{name}.txt")
        progs[name] = {
            "exe_path": exe, "readme_path": d / f"ReadMe_{name}.txt",
            "readme_full_path": None, "label": name, "program_dir": d,
            "icon_path": None,
            "archive_versions": [{"exe_path": exe, "py_path": None,
                                  "has_internal": False, "label": "v1.0.0"}],
        }
    return progs


def dialogs() -> list:
    return [w for w in QApplication.topLevelWidgets()
            if isinstance(w, (QDialog, QMessageBox)) and w.isVisible()]


def name_fits(btn) -> bool:
    fm = QFontMetrics(l._ui_font(9))
    return fm.horizontalAdvance(btn.text()) + 24 + 6 + 16 + 2 <= btn.width()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    l.apply_app_style(app)
    win = l.Launcher()
    win.show()
    progs = make_programs()
    stray = (TMP / "root" / "Foo" / "archive" / "Foo v0.9.0.exe", None)
    win._apply_programs(progs, "Test", TMP / "root", win._scan_gen, tidy=([stray], []))
    app.processEvents()

    # -- nothing pops up; the Clean button says there is something to tidy --
    assert not dialogs(), "a dialog opened by itself after the scan"
    assert win._clean_btn.text() == "Clean (1)", win._clean_btn.text()
    assert win._clean_btn.property("alert") is True
    assert "To tidy" in win.status.text()
    print("  scan with misplaced files: no pop-up, Clean shows (1)")

    it = win._prog_buttons["Image Tools"]
    sc = win._prog_buttons["Screenshots"]
    assert it.text() == "Image Tools" and name_fits(it)

    # -- update highlight on and off: the same button, restyled --
    win._apply_version_updates({"Screenshots"}, {
        "Screenshots": (progs["Screenshots"]["exe_path"],
                        progs["Screenshots"]["archive_versions"])}, win._scan_gen)
    assert win._prog_buttons["Screenshots"] is sc and isValid(sc)
    assert sc.property("update") is True
    win._acknowledge_update("Screenshots")
    assert win._prog_buttons["Screenshots"] is sc and isValid(sc)
    assert sc.property("update") is False
    assert isValid(it)
    assert l.CONFIG_PATH.exists()
    print("  update highlight + acknowledge: restyled in place, config written")

    # -- the version list shows a version found after the card was built --
    progs["Image Tools"]["archive_versions"].append(
        {"exe_path": progs["Image Tools"]["exe_path"], "py_path": None,
         "has_internal": False, "label": "v0.5.0"})
    menu = QMenu()
    win._fill_version_menu(menu, "Image Tools")
    labels = [a.text() for a in menu.actions()]
    assert labels == ["v1.0.0", "v0.5.0"], labels
    print("  version list is read when it opens:", labels)

    # -- moving a program: the other cards stay the same widgets --
    win._move_to_group("Foo", "scripts")
    app.processEvents()
    assert isValid(it) and win._prog_buttons["Image Tools"] is it
    assert isValid(sc) and win._prog_buttons["Screenshots"] is sc
    assert "Foo" in win._prog_buttons and isValid(win._prog_buttons["Foo"])
    assert "(3)" in win._group_toggle_btns["scripts"].text()
    assert "(1)" in win._group_toggle_btns["external"].text()
    print("  move to group: only the two groups changed, counts updated")

    # -- opening a group with a longer name: widths change in place --
    before = it.maximumWidth()
    win._toggle_group("external")
    app.processEvents()
    after = it.maximumWidth()
    assert isValid(it), "the open group's cards were rebuilt"
    assert after > before, (before, after)
    long_btn = win._prog_buttons["A Very Long Program Name Indeed"]
    assert long_btn.text() == "A Very Long Program Name Indeed" and name_fits(long_btn)
    # two cards side by side fit in the window, no horizontal scrolling
    assert win.scroll.viewport().width() >= win._inner.minimumSizeHint().width(), \
        (win.scroll.viewport().width(), win._inner.minimumSizeHint().width())
    win._toggle_group("external")
    app.processEvents()
    assert it.maximumWidth() == before
    assert isValid(sc)
    print(f"  open/close a group: button width {before} -> {after} -> {before}, same widgets")

    # -- moving into an empty group shows its header, moving out hides it --
    win._move_to_group("Screenshots", "personal")
    assert not win._group_toggle_btns["personal"].isHidden()
    win._move_to_group("Screenshots", None)
    assert win._group_toggle_btns["personal"].isHidden()
    print("  empty group header appears and disappears with its programs")

    assert not dialogs(), "a dialog is open at the end"
    win.close()
    print("OK")


if __name__ == "__main__":
    main()
