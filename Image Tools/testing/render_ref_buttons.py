"""LOOK at the Subtraction controls — the checkbox and the Set ref / Remove ref pair.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/render_ref_buttons.py

Remove ref was added beside Set ref. The 275 px sidebar is the whole question: on one
line with the checkbox the second label came out elided, so the pair sits on a row of
its own, half the column each. This renders the real section under main.py's own
stylesheet and prints, per button, whether its label fits the room it got.

Writes ref_buttons.png (both live) and ref_buttons_off.png (Remove ref greyed out,
which is how it sits until a reference is set) beside this file. The window is moved
off the visible desktop before it is shown.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_refbtn_"))
os.environ["APPDATA"] = str(_TMP)

import is_t                                                          # noqa: E402
from PySide6.QtGui import QFontMetrics                               # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                  # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def app_stylesheet() -> str:
    from render_display_rows import app_stylesheet as css
    return css()


def fits(btn) -> "tuple[int, int]":
    """(room it has, room its label needs) — a label needing more is being cut."""
    fm = QFontMetrics(btn.font())
    return btn.width(), fm.horizontalAdvance(btn.text()) + 16


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    else:
        print("WARNING: no app stylesheet read")

    v = is_t.Viewer()
    v.resize(1500, 950)
    v.move(-4000, -4000)
    v.show()
    for c in v.findChildren(QWidget):
        if hasattr(c, "set_expanded"):
            c.set_expanded(True)
    app.processEvents()

    v.btn_set_ref.setEnabled(True)
    app.processEvents()

    box = v.cb_subtract.parentWidget()
    out = Path(__file__).resolve().parent

    for btn, name in ((v.btn_set_ref, "Set ref"), (v.btn_remove_ref, "Remove ref")):
        have, need = fits(btn)
        print(f"  {name:<12} width={have:>4}  label needs={need:>4}")
        check(f"'{name}' is not cut off", need <= have,
              f"needs {need} px, has {have} px")
    # A 1 px difference is the odd leftover width the layout hands to one of them; the
    # rule is that neither is visibly the bigger button.
    check("the two buttons are the same width",
          abs(v.btn_set_ref.width() - v.btn_remove_ref.width()) <= 1,
          f"{v.btn_set_ref.width()} vs {v.btn_remove_ref.width()}")
    check("they are side by side on one row, under the checkbox",
          v.btn_set_ref.y() == v.btn_remove_ref.y()
          and v.btn_set_ref.y() > v.cb_subtract.y(),
          f"set_ref y={v.btn_set_ref.y()}, remove y={v.btn_remove_ref.y()}, "
          f"checkbox y={v.cb_subtract.y()}")
    check("neither sticks out past the panel",
          v.btn_remove_ref.mapTo(box, box.rect().topLeft()).x() <= 0
          or v.btn_remove_ref.geometry().right() <= box.width(),
          f"right edge {v.btn_remove_ref.geometry().right()} of {box.width()}")

    v.btn_remove_ref.setEnabled(True)
    app.processEvents()
    box.grab().save(str(out / "ref_buttons.png"))
    print(f"  wrote ref_buttons.png  ({box.width()}x{box.height()})")

    v.btn_remove_ref.setEnabled(False)
    app.processEvents()
    box.grab().save(str(out / "ref_buttons_off.png"))
    print("  wrote ref_buttons_off.png")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
