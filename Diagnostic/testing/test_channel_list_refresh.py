"""The "Add PV — browse channels" list must offer channels added to the
archiver while the program is open.

On 2026-10-13 the SPIDER GDD, TPD and FOG channels were added to the archiver
mid-session and the browser did not show them: the channel list was fetched
once at start-up and reused for every opening. Now every opening shows the
list it has at once and asks the archiver again in the background.

Checks, all offline (the archiver is replaced by a stand-in):

* a channel added since the last opening appears in the list;
* the main window keeps the fresh list for the next opening;
* the PVs already selected stay selected when the list is refreshed;
* a failed refresh keeps the older list instead of an error and an empty box.

Run with:  python testing/test_channel_list_refresh.py
"""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import monitor_tab as mt  # noqa: E402

_APP = QApplication.instance() or QApplication([])

OLD = ["L3-SBDP-SPIDER:FWHM", "L3-SBDP-SPIDER:FWHM_FL"]
NEW = OLD + ["L3-SBDP-SPIDER:FOG", "L3-SBDP-SPIDER:GDD", "L3-SBDP-SPIDER:TPD"]

failures = []


def check(what, ok, detail=""):
    print(("  PASS  " if ok else "  FAIL  ") + what + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(what)


def settle():
    """Let the background download finish and its answer reach the dialog."""
    QThreadPool.globalInstance().waitForDone(5000)
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        _APP.processEvents()


class Host(QWidget):
    """Stands in for the main window: only the channel list is read."""

    def __init__(self, channels):
        super().__init__()
        self._all_channels = list(channels)


def shown(dlg):
    return [dlg.list.item(i).text() for i in range(dlg.list.count())]


print("=== a channel added since the last opening is offered ===")
mt.api.cpva_fetch_channels = lambda timeout=None: list(NEW)
host = Host(OLD)
dlg = mt.PVBrowserDialog(host, host._all_channels, 5.0)
check("the old list shows at once", shown(dlg) == OLD, str(shown(dlg)))
dlg.list.item(1).setSelected(True)
settle()
check("GDD, TPD and FOG appear after the refresh",
      all(c in shown(dlg) for c in NEW), str(shown(dlg)))
check("the main window keeps the fresh list", host._all_channels == NEW)
check("the PV already selected stays selected",
      [i.text() for i in dlg.list.selectedItems()] == ["L3-SBDP-SPIDER:FWHM_FL"])

print("=== a failed refresh keeps the older list ===")


def boom(timeout=None):
    raise OSError("archiver unreachable")


mt.api.cpva_fetch_channels = boom
dlg2 = mt.PVBrowserDialog(host, host._all_channels, 5.0)
settle()
check("the list is still there", shown(dlg2) == NEW, str(shown(dlg2)))
check("and no failure is announced over it",
      "failed" not in dlg2.count.text().lower(), dlg2.count.text())

print("=== with no list at all a failure is said ===")
empty_host = Host([])
dlg3 = mt.PVBrowserDialog(empty_host, [], 5.0)
settle()
check("the failure is announced", "failed" in dlg3.count.text().lower(), dlg3.count.text())

print()
print("All checks passed." if not failures else f"{len(failures)} check(s) FAILED.")
sys.exit(1 if failures else 0)
