"""Shot Finder number boxes: "0.8" and "0,8" are the same number.

Types both spellings into a band box under an English and a Czech locale and
checks the box holds 0.8 each time (not 8, not refused).

Run:  python testing/test_sf_decimal_comma.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PySide6.QtCore import QLocale                  # noqa: E402
from PySide6.QtWidgets import QApplication          # noqa: E402

import sf_t                                          # noqa: E402

FAILED: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(f"{name} {detail}")


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    for loc_name, loc in (("English", QLocale(QLocale.English, QLocale.UnitedStates)),
                          ("Czech", QLocale(QLocale.Czech, QLocale.CzechRepublic))):
        for typed in ("0.8", "0,8", "12.5", "12,5"):
            sb = sf_t._TrimSpinBox()
            sb.setLocale(loc)
            sb.setRange(-1e6, 1e6)
            sb.setDecimals(3)
            sb.setValue(0.0)
            le = sb.lineEdit()
            le.selectAll()
            le.insert(typed)
            sb.interpretText()
            want = float(typed.replace(",", "."))
            check(f"{loc_name}: typed {typed!r}", abs(sb.value() - want) < 1e-9,
                  f"got {sb.value()}")
    print("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
