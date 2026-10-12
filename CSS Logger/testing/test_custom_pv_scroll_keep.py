"""Editing a custom PV low down in the Custom PVs window keeps the view where
it is — the tables must not jump back to the top after every keystroke.

Headless (real fonts, nothing on screen):

    python testing/test_custom_pv_scroll_keep.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PySide6.QtWidgets import QApplication        # noqa: E402

import main as m                                  # noqa: E402

_app = QApplication.instance() or QApplication([])
FAILURES: "list[str]" = []

CHANNELS = [(chr(65 + i), f"L3-TEST-{i}:Energy", f"Test {i}", True)
            for i in range(8)]
PV_BY_LETTER = {lt: pv for lt, pv, _d, _l in CHANNELS}


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def _pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        _app.processEvents()
        time.sleep(0.01)


def main() -> int:
    print("test_custom_pv_scroll_keep")
    custom = []
    for i in range(24):
        e, b = m._cpv_from_display("A+B*C" if i % 2 else "D/E", PV_BY_LETTER)
        custom.append({"name": f"Formula {i:02d}", "expr": e, "bindings": b})
    dlg = m._CustomPVDialog(custom, channels=CHANNELS)
    dlg.resize(900, 600)
    dlg.show()
    _pump(0.5)

    tbl = dlg._bind_tbl
    bar = tbl.verticalScrollBar()
    check("the bindings table scrolls at all", bar.maximum() > 0, str(bar.maximum()))
    bar.setValue(bar.maximum())
    _pump(0.1)
    before = bar.value()
    rec = dlg._rows[-1]
    for ch in " +A":
        rec["expr"].setText(rec["expr"].text() + ch)
        _pump(0.08)
    _pump(0.4)
    check("typing into the last formula keeps the bindings table down",
          bar.value() > 0 and abs(bar.value() - before) <= max(60, before // 5),
          f"{before} -> {bar.value()}")

    fbar = dlg._formula_scroll.verticalScrollBar()
    if fbar.maximum() > 0:
        fbar.setValue(fbar.maximum())
        _pump(0.1)
        f_before = fbar.value()
        dlg._rows[-1]["name"].setText("Formula renamed")
        _pump(0.4)
        check("renaming keeps the formula list down",
              fbar.value() == f_before, f"{f_before} -> {fbar.value()}")
    dlg.hide()

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    rc = main()
    sys.stdout.flush()
    os._exit(rc)
