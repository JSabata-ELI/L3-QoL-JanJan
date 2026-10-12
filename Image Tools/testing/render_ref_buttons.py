"""LOOK at the Subtraction controls — the checkbox and the Set ref / Remove ref pair.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/render_ref_buttons.py

The 275 px sidebar is the whole question. All three now share ONE row (24.09.2026):
the checkbox on the left and the two buttons filling what is left, in the small button
style — at the normal button size "Remove ref" elided, which is why the pair used to
need a line of its own. This renders the real section under main.py's own stylesheet
and prints, per button, whether its label fits the room it got.

Also checks the other half of the same move: Preload preview left the image settings
and sits beside the Live mode button in Source.

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
    # ONE row for all three: the checkbox and both buttons overlap vertically, and the
    # buttons start to the right of the checkbox. Centres rather than tops, because a
    # small button is shorter than the checkbox beside it.
    def mid(w):
        return w.y() + w.height() // 2
    check("checkbox and both buttons share one row",
          abs(mid(v.btn_set_ref) - mid(v.cb_subtract)) <= 4
          and abs(mid(v.btn_remove_ref) - mid(v.cb_subtract)) <= 4,
          f"checkbox {v.cb_subtract.geometry()}, set {v.btn_set_ref.geometry()}, "
          f"remove {v.btn_remove_ref.geometry()}")
    check("the buttons are to the right of the checkbox",
          v.btn_set_ref.x() >= v.cb_subtract.geometry().right()
          and v.btn_remove_ref.x() >= v.btn_set_ref.geometry().right(),
          f"checkbox right={v.cb_subtract.geometry().right()}, "
          f"set x={v.btn_set_ref.x()}, remove x={v.btn_remove_ref.x()}")
    check("neither sticks out past the panel",
          v.btn_remove_ref.mapTo(box, box.rect().topLeft()).x() <= 0
          or v.btn_remove_ref.geometry().right() <= box.width(),
          f"right edge {v.btn_remove_ref.geometry().right()} of {box.width()}")

    # Preload preview moved up into Source, on the Live mode row.
    cbp = v.cb_preload_preview
    live = v._btn_auto_follow
    check("Preload preview sits beside the Live mode button",
          cbp.parentWidget() is live.parentWidget()
          and abs(mid(cbp) - mid(live)) <= 4
          and cbp.x() >= live.geometry().right(),
          f"live {live.geometry()}, preload {cbp.geometry()}")
    fm_p = QFontMetrics(cbp.font())
    need_p = fm_p.horizontalAdvance(cbp.text()) + 34      # indicator + spacing + padding
    check("'Preload preview' is not cut off", need_p <= cbp.width(),
          f"needs {need_p} px, has {cbp.width()} px")
    src_box = live.parentWidget()
    check("the Source row does not stick out past the panel",
          cbp.geometry().right() <= src_box.width(),
          f"right edge {cbp.geometry().right()} of {src_box.width()}")
    src_box.grab().save(str(Path(__file__).resolve().parent / "source_row.png"))
    print(f"  wrote source_row.png  ({src_box.width()}x{src_box.height()})")

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
