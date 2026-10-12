"""The built-in "Blank" preset: first in the list, empties the PV list, is
never written to the preset file, and cannot be saved over or deleted.

Headless, nothing is written to disk:

    python testing/test_blank_preset.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox  # noqa: E402

import main as css                               # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def main() -> int:
    print("test_blank_preset")
    written = []
    css.save_presets = lambda presets: written.append([p.get("name") for p in presets])
    for fn in ("save_config", "save_condition_presets", "save_custom_pvs"):
        setattr(css, fn, lambda *a, **k: None)
    css.CSSLoggerWidget._maybe_autostart_live = lambda self: None
    QDialog.exec = lambda self, *a, **k: QDialog.DialogCode.Rejected
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

    w = css.CSSLoggerWidget()
    w._request_reload = lambda *a, **k: None
    check("Blank is the first preset", w._preset_combo.itemText(0) == "Blank",
          w._preset_combo.itemText(0))
    check("the program does not open on Blank",
          w._preset_combo.currentText() != "Blank" or len(w._presets) == 1,
          w._preset_combo.currentText())

    w._pv_list.addItem("L3-TEST-A:Energy")
    w._preset_combo.setCurrentIndex(0)          # picking it loads it
    check("Blank empties the PV list", w._real_pv_names() == [], str(w._real_pv_names()))

    w._delete_preset()
    check("Blank cannot be deleted", w._preset_combo.itemText(0) == "Blank"
          and not written, str(written))

    # Save on Blank asks for a new name instead of overwriting it.
    QInputDialog.getText = staticmethod(lambda *a, **k: ("Mine", True))
    w._pv_list.addItem("L3-TEST-B:Energy")
    w._save_preset()
    check("Save on Blank makes a new preset",
          w._presets[0].get("pvs") == [] and any(p["name"] == "Mine" for p in w._presets))
    check("Blank is never written to the file",
          written and all("Blank" not in names for names in written), str(written))

    QInputDialog.getText = staticmethod(lambda *a, **k: ("Blank", True))
    n = len(w._presets)
    w._save_preset_as()
    check("no second preset can be called Blank", len(w._presets) == n)

    w.close()
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
