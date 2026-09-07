"""
End-to-end check against the REAL archiver: load a short window, look at the
table, then export it and read the file back.

Prints what the table holds for each channel (how many of the rows have a
value), so a channel that only ever holds its old value is visible as such, and
then the first rows of the exported file.

Run:  python probe_export_live.py [YYYY-MM-DD HH:MM] [minutes]
"""
import csv
import os
import pathlib
import sys
import tempfile
import time
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import main as app_main                                      # noqa: E402
from cpva_core import load_config, load_custom_pvs, shorten_pv_name  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox        # noqa: E402

app = QApplication.instance() or QApplication([])

# Never touch the user's files from a probe.
for fn in ("save_config", "save_presets", "save_condition_presets",
           "save_custom_pvs"):
    setattr(app_main, fn, lambda *a, **k: None)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.warning     = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
QMessageBox.critical    = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)

when = datetime(2026, 9, 4, 9, 0, 0)
mins = 5
if len(sys.argv) > 1:
    when = datetime.strptime(sys.argv[1], "%Y-%m-%d %H:%M")
if len(sys.argv) > 2:
    mins = int(sys.argv[2])

# The L3 set the shipped formulas are written for, plus whatever the config
# happens to hold — the formulas can only be checked when their sources are in.
_L3 = [
    "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
    "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy",
    "L3-PFWP6-MTR03-1:RawPos",
    "HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy",
    "L3-PM03-023:Energy",
    "L3-PM03-025:Energy",
    "L3-VCS-LN36:OPEN",
    "HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy",
    "L3-TIMING-TIMING:SysRate",
]
cfg = load_config()
pvs = list(_L3)
for pv in (cfg.get("pv_list") or []):
    if pv not in pvs:
        pvs.append(pv)
customs = load_custom_pvs()
print(f"window  {when}  +{mins} min")
print(f"channels {len(pvs)}, custom {len(customs)}")

app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
w = app_main.CSSLoggerWidget()
w._custom_pvs = customs
w._sync_pv_list_customs()
w._pv_list.clear()
for pv in pvs:
    w._pv_list.addItem(pv)
w._sync_pv_list_customs()
w._update_pv_count()
w._dt_from = when
w._dt_to   = when + timedelta(minutes=mins)
w._on_load_clicked(silent=True)

deadline = time.time() + 240
while time.time() < deadline and w._load_in_flight:
    app.processEvents(); time.sleep(0.02)
app.processEvents()
print("status:", w._lbl_status.text())

rows = w._table_rows
print(f"\n{len(rows)} rows")
cols = w._table_pvs()
print(f"{len(cols)} of {len(w._pv_order)} channels are columns")
hidden = [p for p in w._pv_order if p not in cols]
if hidden:
    print("  not a column:", ", ".join(shorten_pv_name(p) for p in hidden))
print()
print(f"{'channel':34s} {'filled':>8s}  {'first':>14s}  {'pre-window':>14s}")
for pv in cols:
    n = sum(1 for _ts, r in rows if r.get(pv, (None,))[0] is not None)
    first = rows[0][1].get(pv, (None,))[0] if rows else None
    pre = w._pre_window_vals.get(pv)
    print(f"{shorten_pv_name(pv)[:34]:34s} {n:5d}/{len(rows):<5d} "
          f"{str(first)[:14]:>14s}  {str(pre[1])[:14] if pre else '-':>14s}")

path = str(pathlib.Path(tempfile.gettempdir()) / "cssl_probe_live.csv")
cfg_exp = {"channels": cols,
           "basis": w._export_shot_channels(cols),
           "source": "archive", "conditions": False, "comma": False}
print("\nshot channels:", ", ".join(shorten_pv_name(p) for p in cfg_exp["basis"])
      or "(none)")
print("  numeric:", ", ".join(shorten_pv_name(p) for p in w._numeric_pvs))
print("  master :", w._get_master_pv())
if not cfg_exp["basis"]:
    # Nothing matched the default rule — export against every numeric column so
    # the probe still exercises the writer.
    cfg_exp["basis"] = [p for p in cols if p in w._numeric_pvs]
    print("  falling back to:",
          ", ".join(shorten_pv_name(p) for p in cfg_exp["basis"]))
w._export_from_archive(path, cfg_exp)
deadline = time.time() + 600
while time.time() < deadline and w._export_prog is not None and w._export_prog.isVisible():
    app.processEvents(); time.sleep(0.02)
app.processEvents()

with open(path, newline="", encoding="utf-8-sig") as f:
    print("\nfile:", f.readline().strip())
    rd = list(csv.reader(f, delimiter=";"))
head, body = rd[0], rd[1:]
print(f"{len(body)} exported rows, {len(head)} columns")
print(" | ".join(head))
for r in body[:5]:
    print(" | ".join(r))
if len(body) > 5:
    print("…")
    for r in body[-2:]:
        print(" | ".join(r))
empty = [head[i] for i in range(len(head))
         if body and all(r[i] == "" for r in body)]
print("\nall-empty columns:", ", ".join(empty) if empty else "none")
