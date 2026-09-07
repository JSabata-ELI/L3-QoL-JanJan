"""
Render the graph for a real window and check every trace spans it.

The point is the derived (formula) channels: they used to start only where all
their sources had a row, so on an archived window in which the sources happened
to stand still they were empty from end to end while the real channels held
their last value across the whole picture.

Writes testing/_out/carry_forward_graph.png and prints, per channel, where its
trace starts and ends relative to the window.

Run:  python probe_carry_forward_graph.py [YYYY-MM-DD HH:MM] [minutes]
"""
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import main as app_main                                      # noqa: E402
from cpva_core import load_custom_pvs, shorten_pv_name, dt_to_ns  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox        # noqa: E402

app = QApplication.instance() or QApplication([])
app_main.install_app_look(app)

for fn in ("save_config", "save_presets", "save_condition_presets",
           "save_custom_pvs"):
    setattr(app_main, fn, lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.warning     = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.critical    = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

# The full L3 set the formulas are written for, not whatever is in the config
# right now.
PVS = [
    "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "L3-PFWP6-MTR03-1:RawPos",
    "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "L3-PM03-023:Energy",
    "L3-PM03-025:Energy",
    "L3-VCS-LN36:OPEN",
    "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
]

when = datetime(2026, 9, 4, 15, 50, 0)
mins = 8
if len(sys.argv) > 1:
    when = datetime.strptime(sys.argv[1], "%Y-%m-%d %H:%M")
if len(sys.argv) > 2:
    mins = int(sys.argv[2])

app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
w = app_main.CSSLoggerWidget()
w.resize(1700, 900)
w.show()
w._custom_pvs = load_custom_pvs()
w._pv_list.clear()
for pv in PVS:
    w._pv_list.addItem(pv)
w._sync_pv_list_customs()
w._update_pv_count()
w._master_multiple_edit.setText("")
w._conditions = []
w._dt_from = when
w._dt_to   = when + timedelta(minutes=mins)
w._on_load_clicked(silent=True)

deadline = time.time() + 300
while time.time() < deadline and w._load_in_flight:
    app.processEvents(); time.sleep(0.02)
for _ in range(20):
    app.processEvents(); time.sleep(0.05)

print("status:", w._lbl_status.text())
print(f"{len(w._table_rows)} rows, {len(w._table_pvs())} columns\n")

lo = dt_to_ns(w._dt_from)
hi = dt_to_ns(w._dt_to)
span = hi - lo


def _num_to_ns(num):
    # _graph_raw holds matplotlib date numbers (days), see _ns_to_num.
    return (float(num) - app_main._mpl_epoch_num()) * app_main._NS_PER_DAY


print(f"{'channel':30s} {'pts':>6s} {'starts at':>10s} {'ends at':>9s}")
short = []
for pv, raw in zip(w._graph_pvs, w._graph_raw):
    ts = raw[0]
    if len(ts) == 0:
        print(f"{shorten_pv_name(pv)[:30]:30s} {'0':>6s}   (nothing drawn)")
        short.append(pv)
        continue
    a = (_num_to_ns(ts[0]) - lo) / span * 100.0
    b = (_num_to_ns(ts[-1]) - lo) / span * 100.0
    print(f"{shorten_pv_name(pv)[:30]:30s} {len(ts):6d} {a:9.1f}% {b:8.1f}%")
    if a > 1.0 or b < 99.0:
        short.append(pv)

out = _HERE / "_out"; out.mkdir(exist_ok=True)
png = out / "carry_forward_graph.png"
w.grab().save(str(png))
print(f"\nwritten {png}")
if short:
    print("does NOT span the window: "
          + ", ".join(shorten_pv_name(p) for p in short))
    sys.exit(1)
print("every drawn channel spans the window")
