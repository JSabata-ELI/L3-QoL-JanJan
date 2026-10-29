"""Laser settings — saved settings: named sets on the share (12.10.2026).

    python testing/test_settings_sets.py

Asked for in these words: "chci, abych měl možnost save settings (všechny v
programu, ...) abych měl možnost nahrát více nastavení podle toho, jaký user
to bude ovládat.. dropdown menu těch nastavení, ze kterých si vyberu, klik na
import a voala.. všechny nastavení se změnili".

  * the rules in `ls_core` (parts, add / overwrite / rename / delete);
  * the round trip through the Settings tab, with a temporary folder standing
    in for the share — saved on one "PC", loaded into another;
  * the DM writer switch is never carried over, a set another PC saved is
    never lost, and a broken file is never overwritten.

Headless, silent and offline: offscreen, and the window is never shown.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FAILED = []


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


def test_rules():
    import ls_core as C
    print("the rules")
    cfg = C.normalise(C.default_config())
    cfg["rows"].insert(0, C.new_row(50, "L3-X:A", "Alpha"))
    cfg["look"]["size_px"] = 33
    C.dm_of(cfg).update({"write": True, "title": "10.0.0.1", "every_s": 3})
    saved = C.settings_parts(cfg, ["channels", "dm"])
    check("only the ticked parts", sorted(saved),
          ["active_hall", "dm", "rows", "scanning", "shooting"])
    check("the DM part holds no writer switch", sorted(saved["dm"]),
          ["every_s", "title"])
    check("parts_in says what it holds", C.parts_in(saved), ["channels", "dm"])

    other = C.normalise(C.default_config())
    C.dm_of(other)["write"] = False
    out = C.apply_settings_parts(other, saved, ["channels", "look", "dm"])
    check("the channels came over",
          [r["name"] for r in out["rows"]][0], "Alpha")
    check("a part the set does not hold is left alone",
          out["look"]["size_px"], other["look"]["size_px"])
    check("the DM title came over", out["dm"]["title"], "10.0.0.1")
    check("the writer switch did not", out["dm"]["write"], False)
    check("the document handed in is untouched", other["rows"] != out["rows"],
          True)

    sets, why = C.add_set({}, "Jan", saved, now_s=1000.0)
    check("added", list(sets), ["Jan"])
    check("same name refused", C.add_set(sets, " Jan ", saved)[0], None)
    check("no name refused", C.add_set(sets, "  ", saved)[0], None)
    check("no parts refused", C.add_set(sets, "Eva", {})[0], None)
    sets, _ = C.add_set(sets, "Eva", saved)
    sets, _ = C.rename_set(sets, "Jan", "Jan M")
    check("renamed, order kept", list(sets), ["Jan M", "Eva"])
    check("rename onto another refused", C.rename_set(sets, "Eva", "Jan M")[0],
          None)
    over, _ = C.overwrite_set(sets, "Eva", {"look": {"size_px": 40}})
    check("overwritten", C.parts_in(over["Eva"]["settings"]), ["look"])
    gone, _ = C.delete_set(over, "Eva")
    check("deleted", list(gone), ["Jan M"])
    doc = json.loads(json.dumps(C.sets_document(gone)))
    check("survives the file", list(C.clean_sets(doc)), ["Jan M"])
    check("a broken entry is dropped",
          list(C.clean_sets({"sets": {"x": 5, "": {"settings": {}},
                                      "ok": {"settings": {}}}})), ["ok"])


def test_through_the_tab():
    print("the Settings tab, against a folder standing in for the share")
    from PySide6.QtWidgets import QApplication

    import ls_core as C
    import ls_dm as D
    import ls_engine

    tmp = tempfile.TemporaryDirectory()
    share_root = Path(tmp.name) / "share"
    share_root.mkdir()
    fake_cfg = Path(tmp.name) / "laser_settings.json"
    C.config_path = lambda: fake_cfg
    ls_engine.PollEngine.start = lambda self: None

    app = QApplication.instance() or QApplication(sys.argv)
    import ls_ui as U
    U.install_app_look(app)
    import main as program

    win = program.LaserSettingsWindow()
    ft = win._font
    ft._sets_share = D.Share(roots=[str(share_root)])

    def wait():
        deadline = time.monotonic() + 10
        while ft._sets_reply is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()

    ft._sets_run(None, "")
    wait()
    check("an empty share: nothing to pick", ft._picked_set(), None)
    check("Load is off", ft._set_load.isEnabled(), False)

    # This PC: two channels, a big text, and it is the DM writer. The fit is
    # off, or it would work the text size out from the window instead.
    win.halls()["fit"] = False
    rows = win.rows()
    rows.insert(0, C.new_row(70, "L3-X:B", "Beta"))
    rows.insert(0, C.new_row(71, "L3-X:A", "Alpha"))
    win.look()["size_px"] = 31
    win.dm_settings()["write"] = True
    win.settings_changed(rebuild=True)

    ft._set_name.setText("Jan")
    ft._set_new_clicked()
    wait()
    path = share_root / D.SHARE_SUBDIR / C.SETS_NAME
    check("written to the share", path.exists(), True)
    check("picked in the drop-down", ft._picked_set(), "Jan")
    check("the name box is cleared", ft._set_name.text(), "")

    # Another PC saves meanwhile, straight into the file.
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["sets"]["Eva"] = {"settings": {"look": {"size_px": 12}},
                          "saved": "", "by": "OTHER-PC"}
    path.write_text(json.dumps(doc), encoding="utf-8")

    # Things change on this PC; then "Jan" is loaded back.
    rows[:] = [r for r in rows if r.get("name") not in ("Alpha", "Beta")]
    win.look()["size_px"] = 15
    win.dm_settings()["write"] = False
    win.settings_changed(rebuild=True)
    ft._set_combo.setCurrentIndex(ft._set_combo.findData("Jan"))
    ft._set_load_clicked()
    wait()
    names = [r.get("name") for r in win.rows()]
    check("the channels are back", names[:2], ["Alpha", "Beta"])
    check("the text size is back", win.look()["size_px"], 31)
    check("the writer switch stayed this PC's",
          win.dm_settings()["write"], False)
    check("the Overview shows them",
          [w.texts()[0] for w in win._overview._row_widgets][:2],
          ["Alpha", "Beta"])
    check("the other PC's set is on the list",
          sorted(ft._sets), ["Eva", "Jan"])
    check("the note says what was loaded",
          ft._set_note.text().startswith("Loaded \"Jan\""), True)

    print("only the ticked parts")
    ft._set_parts["look"].setChecked(False)
    win.look()["size_px"] = 17
    ft._set_load_clicked()
    wait()
    check("Look unticked: the text size is not loaded",
          win.look()["size_px"], 17)
    ft._set_parts["look"].setChecked(True)

    print("rename, overwrite, delete — never another PC's set")
    ft._set_name.setText("Jan M")
    ft._set_rename_clicked()
    wait()
    check("renamed", sorted(ft._sets), ["Eva", "Jan M"])
    ft._set_combo.setCurrentIndex(ft._set_combo.findData("Jan M"))
    ft._set_save_clicked()
    wait()
    check("saved over: the text size now",
          ft._sets["Jan M"]["settings"]["look"]["size_px"], 17)
    ft._set_delete_clicked()
    check("one click only arms", sorted(ft._sets), ["Eva", "Jan M"])
    ft._set_delete_clicked()
    wait()
    check("the second deletes", sorted(ft._sets), ["Eva"])
    on_disk = C.clean_sets(json.loads(path.read_text(encoding="utf-8")))
    check("and the file says so", sorted(on_disk), ["Eva"])

    print("a broken file is never overwritten")
    path.write_text("{ this is not json", encoding="utf-8")
    ft._set_name.setText("New")
    ft._set_new_clicked()
    wait()
    check("refused, said why", "could not be read" in ft._set_note.text(),
          True)
    check("the file is as it was", path.read_text(encoding="utf-8"),
          "{ this is not json")

    for timer in (win._overview._fit_timer, win._overview._blink):
        timer.stop()
    win._save_timer.stop()
    tmp.cleanup()


def main():
    test_rules()
    test_through_the_tab()
    print()
    print("all good" if not FAILED else f"{len(FAILED)} FAILED")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
