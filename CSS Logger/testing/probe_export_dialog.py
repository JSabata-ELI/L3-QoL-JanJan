"""
Render the Export table dialog and check it is legible.

Real Windows platform plugin (offscreen has no fonts and lies about text size).
Writes testing/_out/export_dialog.png and reports anything unreadable it can
measure: white-on-white text, a check box that cannot be seen, text wider than
its widget.

Run:  python probe_export_dialog.py
"""
import os
import pathlib
import sys
from datetime import datetime, timedelta

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import main as app_main                                      # noqa: E402
from PySide6.QtCore import Qt                                 # noqa: E402
from PySide6.QtWidgets import QApplication                    # noqa: E402

app = QApplication.instance() or QApplication([])
app_main.install_app_look(app)

CHANNELS = [
    "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "L3-PFWP6-MTR03-1:RawPos",
    "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "L3-PM03-023:Energy",
    "L3-PM03-025:Energy",
    "L3-VCS-LN36:OPEN",
    "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    "L3-TIMING-TIMING:SysRate",
    "L3-BEAM-FATE:BeamFate",
    "Sum of Green [J]",
    "Beta/Green [%]",
]
CUSTOMS = ["Sum of Green [J]", "Beta/Green [%]"]

now = datetime(2026, 9, 4, 16, 0, 0)
dlg = app_main._ExportTableDialog(
    parent=None,
    channels=CHANNELS,
    display={pv: app_main.shorten_pv_name(pv) for pv in CHANNELS},
    customs=CUSTOMS,
    checked=[pv for pv in CHANNELS if pv != "L3-PM03-023:Energy"],
    shot_channels=[pv for pv in CHANNELS
                   if pv.endswith(":Energy") and "PM03-023" not in pv],
    dt_from=now - timedelta(days=214), dt_to=now,
    has_conditions=True, has_rows=True)
dlg.resize(820, 560)
dlg.show()
for _ in range(12):
    app.processEvents()

out = _HERE / "_out"
out.mkdir(exist_ok=True)
png = out / "export_dialog.png"
dlg.grab().save(str(png))
print(f"written {png}")

problems = []

# Every list row must have visible dark text on a light cell.
for name, lst in (("columns", dlg._lst_cols), ("shots", dlg._lst_basis)):
    for i in range(lst.count()):
        it = lst.item(i)
        fg = it.foreground().color()
        if fg.lightnessF() > 0.6:
            problems.append(f"{name} row {i}: pale text {fg.name()}")
        if not it.text().strip():
            problems.append(f"{name} row {i}: empty label")

# The two lists must be wide enough for the longest name they hold.
for name, lst in (("columns", dlg._lst_cols), ("shots", dlg._lst_basis)):
    fm = lst.fontMetrics()
    widest = max((fm.horizontalAdvance(lst.item(i).text())
                  for i in range(lst.count())), default=0)
    room = lst.viewport().width() - 26          # the check indicator
    if widest > room:
        problems.append(f"{name}: longest name {widest}px, room {room}px "
                        f"(a scrollbar must appear, not clipped text)")

# The window ends up unusable if the buttons or the combo are off-screen.
if dlg._cmb_src.width() < 260:
    problems.append(f"source combo only {dlg._cmb_src.width()}px wide")

print(f"columns list: {dlg._lst_cols.count()} rows, "
      f"{sum(1 for i in range(dlg._lst_cols.count()) if dlg._lst_cols.item(i).checkState() == Qt.CheckState.Checked)} ticked")
print(f"shot list:    {dlg._lst_basis.count()} rows, "
      f"{sum(1 for i in range(dlg._lst_basis.count()) if dlg._lst_basis.item(i).checkState() == Qt.CheckState.Checked)} ticked")
print(f"source:       {dlg._cmb_src.currentText()}")

dlg._accept()
print("cfg:", {k: (len(v) if isinstance(v, list) else v)
               for k, v in dlg.result_cfg.items()})

if problems:
    print("\nPROBLEMS:")
    for p in problems:
        print("  " + p)
    sys.exit(1)
print("\nnothing unreadable measured")
