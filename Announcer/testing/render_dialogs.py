"""Photograph every dialog this program can put on screen.

Run:  python testing/render_dialogs.py
Writes: testing/dlg_*.png

This PC runs Windows in dark mode, and the dialogs the program builds itself
state their own colours — but QMessageBox, QInputDialog and the colour picker
are Qt's own, and they are only as light as the application stylesheet makes
them. That is exactly the kind of thing that has to be LOOKED at rather than
reasoned about, so they are photographed here too.
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QEventLoop, QTimer                    # noqa: E402
from PySide6.QtWidgets import (QApplication, QInputDialog,       # noqa: E402
                               QMessageBox)

app = QApplication.instance() or QApplication([])

import ann_core as C                                             # noqa: E402
import ann_ui as U                                               # noqa: E402

U.install_app_look(app)


def shoot(widget, name, ms=350):
    widget.show()
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    widget.grab().save(str(HERE / f"dlg_{name}.png"))
    print(f"wrote dlg_{name}.png")
    widget.close()


box = QMessageBox(QMessageBox.Icon.Question, "Remove it",
                  'Take "Chiller DA3 — temperature" off the list?\n\nIt is gone '
                  'for good. The standard machine values can be put back with '
                  '"Restore the standard values".',
                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
shoot(box, "confirm")

warn = QMessageBox(QMessageBox.Icon.Warning, "No reference picture",
                   "Without a reference picture this can never fire — there is "
                   "nothing to compare against.\n\nSave it anyway?",
                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
shoot(warn, "warning")

ask = QInputDialog()
ask.setWindowTitle("Save the rectangle")
ask.setLabelText("A name for this rectangle:")
ask.setTextValue("L3BT alignment")
shoot(ask, "ask_name")

import vl_t                                                      # noqa: E402

draft = C.new_value_item(1, "Back reflection", C.DEFAULT_PV, unit="mJ",
                         hi=1.0, hi_hi=2.0,
                         message="Back reflection over the limit")
_cfg = {"alarm_presets": ["Night shift", "Commissioning", "DA1"]}
dlg = vl_t.ValueDialog(None, draft, _cfg)
shoot(dlg, "value_editor", 500)

import ar_t                                                      # noqa: E402


class _FakeWin:
    def hide(self):
        pass

    def show(self):
        pass

    def log(self, *a, **k):
        pass

    def config(self):
        return _cfg


area = C.new_area_item(2, "L3BT alignment check", monitor=1,
                       region=[2919, 166, 3288, 349],
                       message="Alignment check changed")
adlg = ar_t.AreaDialog(None, _FakeWin(), area)
shoot(adlg, "area_editor", 500)

picker = vl_t.ChannelPicker(None, "chl temp")
shoot(picker, "channel_picker", 1200)

import ann_presets as P                                          # noqa: E402

_some = [C.new_value_item(10, "Helium volume", "L3-UTIL-HEB03-001:PressOut_PSI",
                          presets=["Night shift"]),
         C.new_value_item(11, "Alpha voltage", "HAPLS-VOLT_IN_CGL",
                          presets=["Night shift", "Commissioning"]),
         C.new_value_item(12, "Chiller DA1", "L3-UTIL-CHL03-001:Temp")]

shoot(P.ManageDialog(None, dict(_cfg), _some), "presets_manage", 500)
shoot(P.AssignDialog(None, dict(_cfg), _some), "presets_assign", 500)
