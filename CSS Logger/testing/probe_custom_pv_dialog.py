"""Render the "Custom PVs" dialog and save a screenshot.

Checks the two layout complaints: the group-box captions must not be painted
over the tables, and one formula must occupy one block in the bindings table
instead of looking like the same custom PV listed twice.

Run:  set QT_QPA_PLATFORM=windows && python testing/probe_custom_pv_dialog.py
"""
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from PySide6.QtWidgets import QApplication          # noqa: E402
from PySide6.QtGui import QColor, QPalette          # noqa: E402
from PySide6.QtCore import QTimer                   # noqa: E402

import main as m                                    # noqa: E402

CHANNELS = [
    ("A", "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy", "SBW4 - Energy", True),
    ("B", "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy", "PTM1 - Energy", True),
    ("C", "L3-PFWP6-MTR03-1:RawPos",             "L3-PFWP6-MTR03-1:RawPos", True),
    ("D", "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy", "PAP1 - Energy", True),
    ("E", "L3-BR-DIAG:Energy",                   "Back reflection", False),
    ("F", "L3-PM03-025:Energy",                  "PM03-025 - Energy", True),
    ("H", "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy", "PCM4 - Energy", True),
]
PV_BY_LETTER = {lt: pv for lt, pv, _d, _l in CHANNELS}


def _cpv(name, expr):
    e, b = m._cpv_from_display(expr, PV_BY_LETTER)
    return {"name": name, "expr": e, "bindings": b}


CUSTOM = [
    _cpv("Sum of Green [J]",        "F+H"),
    _cpv("Beta/Green [%]",          "A/(F+H)*100"),
    _cpv("PCC1 Eff [%]",            "(F+H)/B*100"),
    _cpv("Split Ratio [%]",         "F/(F+H)*100"),
    _cpv("PTM1/PAP1",               "B/D"),
    _cpv("Comp. Beta Output [J]",   "A*0.749"),
    _cpv("Back Reflection Ratio [%]", "E/(0.749*A)*100"),
]


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(m._APP_STYLESHEET)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor("#F5F5F5"))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor("#212121"))
    pal.setColor(QPalette.ColorRole.Base,            QColor("#FFFFFF"))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor("#E3F2FD"))
    pal.setColor(QPalette.ColorRole.Button,          QColor("#E3F2FD"))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor("#1565C0"))
    app.setPalette(pal)

    dlg = m._CustomPVDialog(CUSTOM, channels=CHANNELS)
    dlg.show()

    out = _HERE / "_out"
    out.mkdir(exist_ok=True)
    path = out / "custom_pv_dialog.png"

    def shoot():
        dlg.grab().save(str(path))
        print("saved", path)
        app.quit()

    QTimer.singleShot(900, shoot)
    app.exec()


if __name__ == "__main__":
    main()
