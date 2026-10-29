"""
Custom PVs (the CSS Logger tab's formulas) as Shot filter conditions.

  * a formula condition fetches its SOURCE channels, never the "ƒ" key;
  * its per-shot values are computed from the sources and the mask follows;
  * a formula using another formula works, and a source with no value gives
    NaN (the shot is rejected), not a crash;
  * an unbound letter is reported in red and does not filter anything;
  * live: the formula's series is built over every source sample time;
  * the Add PV dialog lists the custom PVs, returns "ƒ name", and its name
    column gets the free width.

Runs headless, no archiver needed.

Run:  python test_filter_custom_pv.py   (prints PASS/FAIL, exits non-zero)
"""
import math
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import numpy as np                                           # noqa: E402
from PySide6.QtWidgets import QApplication                   # noqa: E402

import sp_t                                                  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

app = QApplication.instance() or QApplication(sys.argv)
sp_t.SpectraWidget._save_shot_filter = lambda self: None     # keep the user's file
sp_t._cpva_load_all_channels = lambda: []                    # no archiver asked

failures = []


def check(what, ok):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


ENV = {"__builtins__": {}, "abs": abs, "min": min, "max": max,
       "round": round, "math": math}
DEFS = []


def _defs():
    out = []
    for name, expr, srcs, problem in DEFS:
        try:
            code = compile(expr, "<cpv>", "eval")
        except Exception:
            code = None
        out.append({"name": name, "expr": expr, "code": code,
                    "srcs": srcs, "problem": problem})
    return out


w = sp_t.SpectraWidget()
w.set_custom_pv_host(_defs, lambda parent: False, ENV)
P = sp_t.CPV_PREFIX

DEFS[:] = [("Sum", "A+B", [("A", "CH:A"), ("B", "CH:B")], ""),
           ("Twice", "C*2", [("C", "Sum")], ""),
           ("Broken", "A+Q", [("A", "CH:A")], "Q is not bound to a PV")]

w._filter_on = True
w._filter_conds = [{"label": "Sum", "channel": P + "Sum", "value": 5.0,
                    "tol": 0.0, "on": True}]
check("sources are fetched, not the ƒ key",
      w._filter_channels() == ["CH:A", "CH:B"])

n = 4
r = {"id": 1, "analyzed": True, "n_all": n, "stack_ts_all": [10, 20, 30, 40],
     "stack_all": np.zeros((n, 3)),
     "shot_vals": {"CH:A": np.array([1.0, 2.0, 3.0, np.nan]),
                   "CH:B": np.array([4.0, 3.0, 9.0, 1.0])}}
w._derive_custom_shot_vals(r)
v = r["shot_vals"].get(P + "Sum")
check("formula values per shot (NaN source -> NaN)",
      v is not None and list(v[:3]) == [5.0, 5.0, 12.0] and np.isnan(v[3]))
m = w._region_filter_mask(r)
check("mask keeps the shots where A+B = 5",
      m is not None and list(m) == [True, True, False, False])

w._filter_conds = [{"label": "Twice", "channel": P + "Twice", "value": 24.0,
                    "tol": 0.0, "on": True}]
check("formula of a formula fetches the base channels",
      w._filter_channels() == ["CH:A", "CH:B"])
w._derive_custom_shot_vals(r)
m = w._region_filter_mask(r)
check("formula of a formula filters",
      m is not None and list(m) == [False, False, True, False])

w._filter_conds = [{"label": "Broken", "channel": P + "Broken", "value": 1.0,
                    "tol": 0.0, "on": True}]
check("broken formula fetches nothing", w._filter_channels() == [])
w._derive_custom_shot_vals(r)
check("broken formula filters nothing", w._region_filter_mask(r) is None)
text, style = w._filter_row_note(w._filter_conds[0])
check("broken formula note is red and says why",
      "Q is not bound" in text and "B71C1C" in style)

# Live: the formula's series over every source sample time.
w._filter_conds = [{"label": "Sum", "channel": P + "Sum", "value": 5.0,
                    "tol": 0.0, "on": True}]
w._live_scalars = {"CH:A": [(100, 1.0), (300, 2.0)],
                   "CH:B": [(200, 4.0)]}
w._derive_live_custom()
ser = w._live_scalars.get(P + "Sum")
check("live series over the union of times",
      ser is not None and [t for t, _ in ser] == [100, 200, 300]
      and np.isnan(ser[0][1]) and ser[1][1] == 5.0 and ser[2][1] == 6.0)

# The dialog.
dlg = w._filter_pv_dialog()
items = [dlg._lst.item(i) for i in range(dlg._lst.count())]
keys = [it.data(sp_t.Qt.ItemDataRole.UserRole) for it in items]
check("dialog lists the custom PVs with nothing typed",
      keys == [P + "Sum", P + "Twice", P + "Broken"])
items[0].setSelected(True)
dlg.resize(600, 480)
dlg._fit_pick_columns()
tbl = dlg._tbl_pick
check("picked custom PV shows in the name table",
      tbl.rowCount() == 1 and tbl.item(0, 0).text() == "Sum")
dlg._on_add()
check("dialog returns (name, 'ƒ name')", dlg.added_pvs() == [("Sum", P + "Sum")])

dlg2 = sp_t.PvSearchDialog(None)
check("dialog without a host lists no custom PVs", dlg2._lst.count() == 0)

print("PASS" if not failures else f"FAIL ({len(failures)})")
sys.exit(1 if failures else 0)
