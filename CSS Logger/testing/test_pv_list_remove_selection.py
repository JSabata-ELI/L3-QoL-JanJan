"""
Where the highlight goes after "✕ Remove" in the PV LIST.

  * removing a PV highlights the one that slid up into its place (the one
    that was below it);
  * removing the last PV highlights the one above it;
  * the custom (ƒ) rows at the bottom are never highlighted — they are skipped
    the same way as the end of the list;
  * removing several at once highlights the PV now at the first freed place;
  * with no PV left nothing is highlighted.

Also checks the PV LIST row carries "+ Add PV" and "Custom PV", and that no tab
row still has its own "Add custom PV" button.

Runs headless, no archiver needed.

Run:  python test_pv_list_remove_selection.py   (prints PASS/FAIL, exits non-zero)
"""
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import main as app_main                                      # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton     # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
w = app_main.CSSLoggerWidget()
w._request_reload = lambda *a, **k: None                      # no archiver here
w._custom_pvs = [{"name": "CUSTOM_ONE", "expr": "A"}]

failures = []


def check(what, ok):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


def fill(names):
    w._pv_list.clear()
    for n in names:
        w._pv_list.addItem(n)
    w._sync_pv_list_customs()


def pick(*rows):
    lst = w._pv_list
    lst.clearSelection()
    for r in rows:
        lst.item(r).setSelected(True)


def selected():
    return [it.text() for it in w._pv_list.selectedItems()]


# One from the middle -> the one below.
fill(["A", "B", "C", "D"])
pick(1)
w._remove_selected_pvs()
check("middle removed -> the PV below is highlighted", selected() == ["C"])

# Another Remove straight away takes that one, and so on.
w._remove_selected_pvs()
check("second Remove takes the highlighted one", w._real_pv_names() == ["A", "D"]
      and selected() == ["D"])

# The last one -> the one above (the custom row below is skipped).
w._remove_selected_pvs()
check("last removed -> the PV above is highlighted", selected() == ["A"])

# Several at once -> the PV now at the first freed place.
fill(["A", "B", "C", "D", "E"])
pick(1, 3)
w._remove_selected_pvs()
check("B and D removed -> C is highlighted", selected() == ["C"])

# Everything -> nothing highlighted.
fill(["A", "B"])
pick(0, 1)
w._remove_selected_pvs()
check("all removed -> nothing highlighted", selected() == []
      and w._pv_list.currentRow() == -1)

# Nothing selected -> Remove does nothing and highlights nothing.
fill(["A", "B"])
w._pv_list.clearSelection()
w._pv_list.setCurrentRow(-1)
w._remove_selected_pvs()
check("nothing selected -> nothing removed", w._real_pv_names() == ["A", "B"]
      and selected() == [])

labels = [b.text() for b in w.findChildren(QPushButton)]
check("'+ Add PV' button exists", "+ Add PV" in labels)
check("'Custom PV' button exists", "Custom PV" in labels)
check("'Browse' button is gone", "Browse" not in labels)
check("no 'Add custom PV' button left in the tabs", "Add custom PV" not in labels)

print("PASS" if not failures else f"FAIL ({len(failures)})")
sys.exit(1 if failures else 0)
