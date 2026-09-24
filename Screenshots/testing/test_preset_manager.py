"""Preset Manager: add, remove, rename, reorder by drag, camera search, row height.

The preset list used to be "built-ins from the source code + custom ones appended",
so nothing could be deleted or moved. It is now one editable, ordered list kept in
custom_presets.json (v2). This checks the whole round trip, including that the new
order reaches the preset buttons in the main window.

The window is built far off the desktop (offscreen=True), so nothing appears on
screen and no picture is taken — sizes are measured with bbox/winfo.

Run:  python testing/test_preset_manager.py
"""
import json
import sys
import tempfile
import tkinter.font as tkfont
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s  # noqa: E402

FAILS = []
TMP = Path(tempfile.gettempdir()) / "screenshots_test_presets.json"


def check(label, ok, extra=""):
    print(f"  {'OK ' if ok else 'BAD'}  {label}{('  - ' + extra) if extra else ''}")
    if not ok:
        FAILS.append(label)


def new_app():
    """An App whose preset file is the temporary one."""
    s.App._presets_path = lambda self: TMP
    app = s.App()
    app.withdraw()
    app.update()
    return app


def tree_names(dlg):
    tree = find_tree(dlg)
    return [tree.item(iid, "values")[0] for iid in tree.get_children("")]


def find_tree(w):
    import tkinter.ttk as ttk
    if isinstance(w, ttk.Treeview):
        return w
    for ch in w.winfo_children():
        found = find_tree(ch)
        if found is not None:
            return found
    return None


def find_widgets(w, kind, out=None):
    out = [] if out is None else out
    if isinstance(w, kind):
        out.append(w)
    for ch in w.winfo_children():
        find_widgets(ch, kind, out)
    return out


def button(dlg, text):
    import tkinter.ttk as ttk
    for b in find_widgets(dlg, ttk.Button):
        if str(b.cget("text")) == text:
            return b
    return None


def main():
    import tkinter.ttk as ttk
    s._set_dpi_awareness()

    # ── 1. migration: a v1 flat file keeps its order and merges over the built-ins
    TMP.write_text(json.dumps({"My set": {"cams": ["SAM2_FF"], "mons": None}}), encoding="utf-8")
    app = new_app()
    names = list(app._presets)
    check("v1 file migrates", names[:len(s.PRESETS)] == list(s.PRESETS) and names[-1] == "My set",
          f"{names[-3:]}")
    check("built-in cameras survive migration",
          app._presets["LT2"]["cams"] == list(s.PRESETS["LT2"]["cams"]))
    check("built-in monitors survive migration", app._presets["PLFE"]["mons"] == [6])
    app.destroy()

    # ── seeding from the factory list when there is no file
    TMP.unlink(missing_ok=True)
    app = new_app()
    check("no file seeds the built-ins", list(app._presets) == list(s.PRESETS))
    check("seed is a private copy",
          app._presets["LT1"]["cams"] is not s.PRESETS["LT1"]["cams"])

    dlg = app._open_preset_manager(offscreen=True)
    app.update()
    tree = find_tree(dlg)

    # ── 2. rows are tall enough for descenders
    f = tkfont.nametofont("TkDefaultFont")
    need = f.metrics("linespace")
    rowheight = int(ttk.Style(dlg).lookup("Presets.Treeview", "rowheight"))
    check("row height fits the font", rowheight >= need + 4, f"rowheight {rowheight}, font {need}")
    first = tree.get_children("")[0]
    bbox = tree.bbox(first)
    check("a real row is that tall", bool(bbox) and bbox[3] >= need + 4,
          f"bbox {bbox}, font {need}")
    btn = app.preset_buttons["PL - High Power"]
    app.update_idletasks()
    check("main-window preset button is not clipped",
          btn.winfo_height() >= need + 2, f"button {btn.winfo_height()} px, font {need}")

    # ── 3. one caption only, no column header
    check("no column header in the list", str(tree.cget("show")) == "")
    captions = [str(lf.cget("text")) for lf in find_widgets(dlg, ttk.LabelFrame)]
    check("exactly one 'Presets' caption", captions.count("Presets") == 1, f"{captions}")

    # ── 4. add
    button(dlg, "New").invoke()
    name_entry = find_widgets(dlg, ttk.Entry)[0]
    name_entry.insert(0, "Test set")
    boxes = find_widgets(dlg, ttk.Checkbutton)
    boxes[0].invoke()
    boxes[1].invoke()
    button(dlg, "Save").invoke()
    app.update()
    check("new preset lands at the end", list(app._presets)[-1] == "Test set")
    check("new preset has its cameras", len(app._presets["Test set"]["cams"]) == 2,
          str(app._presets["Test set"]["cams"]))
    check("new preset shows in the list", tree_names(dlg)[-1] == "Test set")
    check("new preset has a button", "Test set" in app.preset_buttons)
    saved = json.loads(TMP.read_text(encoding="utf-8"))
    check("saved as v2", saved.get("version") == 2 and "Test set" in saved.get("presets", {}))

    # ── rename keeps the slot
    pos = list(app._presets).index("LT2")
    for iid, nm in [(i, tree.item(i, "values")[0]) for i in tree.get_children("")]:
        if nm == "LT2":
            tree.selection_set(iid)
            break
    app.update()
    name_entry.delete(0, "end")
    name_entry.insert(0, "LT2 renamed")
    button(dlg, "Save").invoke()
    app.update()
    check("rename keeps the position", list(app._presets).index("LT2 renamed") == pos,
          f"{pos} -> {list(app._presets).index('LT2 renamed')}")
    check("old name is gone", "LT2" not in app._presets)
    check("renamed preset keeps its monitors", app._presets["LT2 renamed"]["mons"] is None)

    # a duplicate name is refused
    name_entry.delete(0, "end")
    name_entry.insert(0, "LT5")
    button(dlg, "Save").invoke()
    app.update()
    check("duplicate name refused", list(app._presets).count("LT5") == 1
          and "LT2 renamed" in app._presets)

    # ── 5. drag to reorder, and the main window follows
    order_before = list(app._presets)
    tree.yview_moveto(0)
    app.update()
    src_iid = tree.get_children("")[5]
    dst_iid = tree.get_children("")[1]
    src_y = tree.bbox(src_iid)[1] + 2
    dst_y = tree.bbox(dst_iid)[1] + 2
    tree.event_generate("<ButtonPress-1>", x=10, y=src_y)
    tree.event_generate("<B1-Motion>", x=10, y=dst_y)
    tree.event_generate("<ButtonRelease-1>", x=10, y=dst_y)
    app.update()
    moved = order_before[5]
    expect = [n for n in order_before if n != moved]
    expect.insert(1, moved)
    check("drag moved the row", list(app._presets) == expect,
          f"{list(app._presets)[:4]} vs {expect[:4]}")
    check("the list shows the new order", tree_names(dlg) == expect)
    check("the new order is saved",
          list(json.loads(TMP.read_text(encoding='utf-8'))["presets"]) == expect)
    app.update_idletasks()
    grid_order = sorted(app.preset_buttons,
                        key=lambda n: (app.preset_buttons[n].grid_info()["row"],
                                       app.preset_buttons[n].grid_info()["column"]))
    check("the main-window buttons follow", grid_order == expect, f"{grid_order[:4]}")

    # ── 6. remove
    victim = "LT2 renamed"
    for iid in tree.get_children(""):
        if tree.item(iid, "values")[0] == victim:
            tree.selection_set(iid)
            break
    app.update()
    s.messagebox.askyesno = lambda *a, **k: True
    button(dlg, "Remove").invoke()
    app.update()
    check("removed from the list", victim not in app._presets)
    check("removed from the table", victim not in tree_names(dlg))
    check("removed from the buttons", victim not in app.preset_buttons)
    check("removed from the file",
          victim not in json.loads(TMP.read_text(encoding="utf-8"))["presets"])

    # a built-in can be removed too, and the monitor counts stay clean
    for iid in tree.get_children(""):
        if tree.item(iid, "values")[0] == "PLFE":
            tree.selection_set(iid)
            break
    app.update()
    app.toggle_preset("PLFE")          # make it active first
    check("active preset counted", bool(app._preset_mon_ref) or app._preset_all_screens_ref > 0)
    button(dlg, "Remove").invoke()
    app.update()
    check("built-in removed", "PLFE" not in app._presets)
    check("monitor counts unwound", not app._preset_mon_ref and app._preset_all_screens_ref == 0,
          f"{app._preset_mon_ref} {app._preset_all_screens_ref}")
    check("not left active", "PLFE" not in app._active_presets)

    # ── 7. restore defaults
    button(dlg, "Restore defaults").invoke()
    app.update()
    check("built-in is back", "PLFE" in app._presets)
    check("custom preset untouched", "Test set" in app._presets)
    check("restored cameras are the factory ones",
          app._presets["PLFE"]["cams"] == list(s.PRESETS["PLFE"]["cams"]))

    # ── 8. camera search
    entries = find_widgets(dlg, ttk.Entry)
    search_entry = entries[1]
    all_boxes = find_widgets(dlg, ttk.Checkbutton)
    # tick a camera that the search will hide
    target = None
    for b in all_boxes:
        if str(b.cget("text")) == "PFM1_NF":
            target = b
            break
    button(dlg, "New").invoke()
    name_entry.delete(0, "end")
    name_entry.insert(0, "Search set")
    target.invoke()
    search_entry.insert(0, "sam ff")
    app.update()
    shown = [str(b.cget("text")) for b in all_boxes if b.grid_info()]
    check("search filters the grid", shown and all("sam" in t.lower() and "ff" in t.lower()
                                                   for t in shown), f"{shown[:4]} ({len(shown)})")
    check("search hides the rest", "PFM1_NF" not in shown)
    button(dlg, "Save").invoke()
    app.update()
    check("a hidden tick is still saved", app._presets["Search set"]["cams"] == ["PFM1_NF"],
          str(app._presets["Search set"]["cams"]))
    search_entry.delete(0, "end")
    app.update()
    check("clearing the search shows all",
          len([b for b in all_boxes if b.grid_info()]) == len(all_boxes))

    # ── 9. the order survives a restart
    expect_final = list(app._presets)
    dlg.destroy()
    app.destroy()
    app2 = new_app()
    check("order survives a restart", list(app2._presets) == expect_final,
          f"{list(app2._presets)[:4]}")
    check("buttons rebuilt in that order",
          app2._preset_names == expect_final)
    app2.destroy()

    TMP.unlink(missing_ok=True)
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
