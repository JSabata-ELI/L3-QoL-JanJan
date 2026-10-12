"""Changes update the window in place; nothing pops up by itself.

Run from the Launcher folder:  python testing/test_inplace_updates.py

What used to go wrong: acknowledging an update, the 10 s version poll, moving a
program to another group and opening a group with a longer name all tore down
every card and built the window again (the visible flicker). The 🔽 list kept
the versions of the first scan. After every scan, the tidy-up dialogs opened by
themselves and took the keyboard.
"""
import tempfile
import tkinter as tk
from pathlib import Path

import hidden_tk  # noqa: F401  (before `import l`)
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
    for name in ("Image Tools", "Screenshots", "Foo", "A Very Long Program Name"):
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


def toplevels(app) -> list:
    return [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]


def main():
    app = l.Launcher()
    progs = make_programs()
    stray = (TMP / "root" / "Foo" / "archive" / "Foo v0.9.0.exe", None)
    app._apply_programs(progs, "Test", TMP / "root", app._scan_gen, tidy=([stray], []))
    app.update_idletasks()

    # -- nothing pops up; the Clean button says there is something to tidy --
    assert not toplevels(app), "a dialog opened by itself after the scan"
    assert app._clean_btn.cget("text") == "🧹 Clean (1)", app._clean_btn.cget("text")
    assert str(app._clean_btn.cget("style")) == "Alert.TButton"
    assert "To tidy" in app.status.cget("text")
    print("  scan with misplaced files: no pop-up, Clean shows (1)")

    it = app._prog_buttons["Image Tools"]
    sc = app._prog_buttons["Screenshots"]

    # -- update highlight on and off: the same button, restyled --
    app._apply_version_updates({"Screenshots"}, {
        "Screenshots": (progs["Screenshots"]["exe_path"],
                        progs["Screenshots"]["archive_versions"])}, app._scan_gen)
    assert app._prog_buttons["Screenshots"] is sc and sc.winfo_exists()
    assert str(sc.cget("style")) == "Update.Prog.TButton", sc.cget("style")
    app._acknowledge_update("Screenshots")
    assert app._prog_buttons["Screenshots"] is sc and sc.winfo_exists()
    assert str(sc.cget("style")) == "Prog.TButton"
    assert it.winfo_exists()
    print("  update highlight + acknowledge: restyled in place")

    # -- the 🔽 list shows a version found after the card was built --
    progs["Image Tools"]["archive_versions"].append(
        {"exe_path": progs["Image Tools"]["exe_path"], "py_path": None,
         "has_internal": False, "label": "v0.5.0"})
    menu = tk.Menu(app, tearoff=0)
    app._fill_version_menu(menu, "Image Tools")
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
    assert labels == ["v1.0.0", "v0.5.0"], labels
    print("  version list is read when it opens:", labels)

    # -- moving a program: the other cards stay the same widgets --
    app._move_to_group("Foo", "scripts")
    assert it.winfo_exists() and app._prog_buttons["Image Tools"] is it
    assert sc.winfo_exists() and app._prog_buttons["Screenshots"] is sc
    assert "Foo" in app._prog_buttons and app._prog_buttons["Foo"].winfo_exists()
    assert "(3)" in app._group_toggle_btns["scripts"].cget("text")
    assert "(1)" in app._group_toggle_btns["external"].cget("text")
    print("  move to group: only the two groups changed, counts updated")

    # -- opening a group with a longer name: widths change in place --
    before = int(str(it.cget("width")))
    app._toggle_group("external")
    after = int(str(it.cget("width")))
    assert it.winfo_exists(), "the open group's cards were rebuilt"
    assert after > before, (before, after)
    app._toggle_group("external")
    assert int(str(it.cget("width"))) == before
    assert sc.winfo_exists()
    print(f"  open/close a group: button width {before} -> {after} -> {before}, same widgets")

    # -- moving into an empty group shows its header, moving out hides it --
    app._move_to_group("Screenshots", "personal")
    assert app._group_headers["personal"].winfo_manager() == "pack"
    app._move_to_group("Screenshots", None)
    assert app._group_headers["personal"].winfo_manager() == ""
    print("  empty group header appears and disappears with its programs")

    # -- the mouse wheel changes a value only in the field that has focus --
    # (a class Tk gives no wheel binding has nothing to guard)
    guard = 'if {[focus] ne "%W"} continue'
    assert app.bind_class("TCombobox", "<MouseWheel>").startswith(guard)
    for cls in app._WHEEL_VALUE_CLASSES:
        script = app.bind_class(cls, "<MouseWheel>")
        assert not script or script.startswith(guard), (cls, script[:60])
    print("  wheel guard on value fields")

    assert not toplevels(app), "a dialog is open at the end"
    app.destroy()
    print("OK")


if __name__ == "__main__":
    main()
