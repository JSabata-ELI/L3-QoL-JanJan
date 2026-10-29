"""Preset Save / Save as new / Load against a shared preset file.

The case that lost two PVs: the file held two presets named "Operation"; Save
wrote into the second, the next start opened the first. Also covered: a second
station still running with an old list must not undo a save on its next write,
and Save must keep master PV / multiple / conditions.

Headless; the preset "file" is a list in memory, nothing is written to disk:

    python testing/test_preset_save_load.py
"""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402

import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []

OLD_OP = ["L3-A:Energy", "L3-B:Energy"]
NEW_OP = OLD_OP + ["L3-SBDP-SPIDER:FWHM", "HAPLS-ENER_IN_PAM11_LT7_DIAG2:Energy"]

FILE: list = [
    {"name": "LT7", "pvs": ["L3-X:Energy"]},
    {"name": "Operation", "pvs": list(OLD_OP)},
    {"name": "Tuning", "pvs": ["L3-Y:Energy"], "conditions": [{"pv": "c", "min": 1}],
     "master_pv": "L3-M:RawPos", "master_multiple": "1000"},
    {"name": "Operation", "pvs": list(NEW_OP)},     # duplicate, newer content
]


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def by_name(name):
    return [p for p in FILE if p.get("name") == name]


def new_widget():
    w = css.CSSLoggerWidget()
    w._request_reload = lambda *a, **k: None
    return w


def set_pvs(w, pvs):
    w._pv_list.clear()
    for pv in pvs:
        w._pv_list.addItem(w._make_pv_item(pv))


def main() -> int:
    print("test_preset_save_load")
    css.load_presets = lambda: copy.deepcopy(FILE)

    def _save(presets):
        FILE[:] = copy.deepcopy(presets)
    css.save_presets = _save
    for fn in ("save_config", "save_condition_presets", "save_custom_pvs"):
        setattr(css, fn, lambda *a, **k: None)
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

    # 1. A file with two "Operation" entries opens on the newer content.
    a = new_widget()
    names = [a._preset_combo.itemText(i) for i in range(a._preset_combo.count())]
    check("duplicate names collapse to one", names.count("Operation") == 1, str(names))
    check("startup opens Operation", a._preset_combo.currentText() == "Operation")
    check("startup shows the newer PV list", a._real_pv_names() == NEW_OP,
          str(a._real_pv_names()))

    # 2. Station B starts now; station A then adds a PV and saves.
    b = new_widget()
    set_pvs(a, NEW_OP + ["L3-EXTRA:Energy"])
    a._save_preset()
    check("Save writes one Operation", len(by_name("Operation")) == 1,
          str(len(by_name("Operation"))))
    check("Save writes the PVs on screen",
          by_name("Operation")[0]["pvs"] == NEW_OP + ["L3-EXTRA:Energy"])

    # 3. B (old list in memory) saves a different preset: A's save survives.
    b._preset_combo.setCurrentIndex(b._preset_combo.findText("LT7"))
    set_pvs(b, ["L3-X:Energy", "L3-Z:Energy"])
    b._save_preset()
    check("another station's save keeps this one",
          by_name("Operation")[0]["pvs"] == NEW_OP + ["L3-EXTRA:Energy"],
          str(by_name("Operation")[0]["pvs"]))
    check("and writes its own", by_name("LT7")[0]["pvs"] == ["L3-X:Energy", "L3-Z:Energy"])

    # 4. Load on A brings B's change in.
    a._preset_combo.setCurrentIndex(a._preset_combo.findText("LT7"))
    check("Load reads the file again", a._real_pv_names() == ["L3-X:Energy", "L3-Z:Energy"],
          str(a._real_pv_names()))

    # 5. Save keeps keys this tab does not edit, and the master PV.
    a._preset_combo.setCurrentIndex(a._preset_combo.findText("Tuning"))
    check("Load applies the master PV", a._master_pv_edit is None
          or a._master_pv_edit.text() == "L3-M:RawPos")
    a._save_preset()
    t = by_name("Tuning")[0]
    check("Save keeps conditions", t.get("conditions") == [{"pv": "c", "min": 1}], str(t))
    check("Save keeps master PV / multiple", a._master_pv_edit is None
          or (t.get("master_pv") == "L3-M:RawPos" and t.get("master_step") == "1000"),
          str(t))

    # 6. Save as new under an existing name overwrites, after a Yes.
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Operation", True))
    set_pvs(a, ["L3-ONLY:Energy"])
    a._save_preset_as()
    check("Save as new on an existing name makes no duplicate",
          len(by_name("Operation")) == 1)
    check("and overwrites it after Yes", by_name("Operation")[0]["pvs"] == ["L3-ONLY:Energy"])
    check("the combo stays on it", a._preset_combo.currentText() == "Operation")
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
    set_pvs(a, ["L3-NOPE:Energy"])
    a._save_preset_as()
    check("No leaves it alone", by_name("Operation")[0]["pvs"] == ["L3-ONLY:Energy"])

    # 7. Save as new with a fresh name adds it; Delete removes only it.
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Fresh", True))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    a._save_preset_as()
    check("Save as new adds a preset", len(by_name("Fresh")) == 1)
    check("and selects it", a._preset_combo.currentText() == "Fresh")
    a._delete_preset()
    check("Delete removes it", not by_name("Fresh"))
    check("Delete keeps the rest", [p["name"] for p in FILE] ==
          ["LT7", "Operation", "Tuning"], str([p["name"] for p in FILE]))
    check("Blank never reaches the file", not by_name("Blank"))

    a.close(); b.close()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
