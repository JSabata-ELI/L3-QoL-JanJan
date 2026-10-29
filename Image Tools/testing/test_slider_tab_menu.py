"""Layout tabs: right-click menu and the look of the selected tab.

Claims:
  1. The menu on a tab offers New tab, Duplicate, Rename, Move left / right, Close
     and Close other tabs; on the empty bar only New tab. Close (and Close other
     tabs) are off when there is one tab, Move left is off on the first tab.
  2. Duplicate puts "<name> (copy)" right after the tab, with the same state
     (cameras, window), makes it current and saves it to slider_tabs.json.
  3. Move right swaps the tab and its page.
  4. The selected tab is light blue with a 2 px dark-blue rim; the others white.

Runs headless:  python testing/test_slider_tab_menu.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="it_tabs_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication, QWidget         # noqa: E402

import slider_tabs                                          # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


class _FakeViewer(QWidget):
    """Stands in for is_t.Viewer: only what SliderTabs calls."""
    def __init__(self):
        super().__init__()
        self.state = {}
        self.last_pick_cam_names = []

    def export_tab_state(self):
        return dict(self.state)

    def set_pending_tab_state(self, st):
        self.state = dict(st)

    def start_from_tab_state(self):
        return bool(self.state)

    def auto_start_online(self):
        pass

    def suspend(self):
        pass

    def resume(self):
        pass

    def drop_preview(self):
        pass


def actions(menu):
    return {a.text(): a.isEnabled() for a in menu.actions() if a.text()}


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    tabs = slider_tabs.SliderTabs(_FakeViewer)
    tabs.set_outer_visible(True)
    app.processEvents()

    print("Menu")
    m = actions(tabs._tab_menu(0))
    check("all entries", list(m) == ["New tab", "Duplicate", "Rename", "Move left",
                                      "Move right", "Close", "Close other tabs"], str(list(m)))
    check("one tab: Close off", not m["Close"] and not m["Close other tabs"])
    check("first tab: Move left off", not m["Move left"])
    check("empty bar: only New tab", list(actions(tabs._tab_menu(-1))) == ["New tab"])

    print("Duplicate")
    page0 = tabs._tabs.widget(0)
    tabs._build_viewer(page0)
    page0.viewer.state = {"cameras": ["CAM_A", "CAM_B"], "live": False,
                          "window": {"date": "2026-10-20", "h_from": 8}}
    name0 = tabs._tabs.tabText(0)
    tabs.new_tab("Other")                    # a second tab after it
    tabs._tabs.setCurrentIndex(0)
    new = tabs.duplicate_page(page0)
    check("copy right after the original", tabs._tabs.indexOf(new) == 1)
    check("copy name", tabs._tabs.tabText(1) == f"{name0} (copy)", tabs._tabs.tabText(1))
    check("copy is current", tabs._tabs.currentWidget() is new)
    st = tabs._page_state(new)
    check("same cameras and window", st.get("cameras") == ["CAM_A", "CAM_B"]
          and st.get("window", {}).get("h_from") == 8, str(st))
    saved = json.loads(slider_tabs.TABS_PATH.read_text(encoding="utf-8"))
    check("saved", [t["name"] for t in saved["tabs"]]
          == [name0, f"{name0} (copy)", "Other"], str([t["name"] for t in saved["tabs"]]))
    again = tabs.duplicate_page(page0)
    check("second copy gets its own name",
          tabs._tabs.tabText(tabs._tabs.indexOf(again)) == f"{name0} (copy) 2")

    print("Move right")
    p_first, p_second = tabs._tabs.widget(0), tabs._tabs.widget(1)
    tabs._move_tab(0, 1)
    check("tab and page moved together", tabs._tabs.widget(0) is p_second
          and tabs._tabs.widget(1) is p_first)
    m = actions(tabs._tab_menu(1))
    check("several tabs: Close on", m["Close"] and m["Close other tabs"])

    print("Selected tab look")
    qss = slider_tabs._TAB_QSS
    check("selected is light blue with a 2 px blue rim",
          "#bbdefb" in qss and "2px solid #1565c0" in qss)
    check("others are white", "background: #ffffff" in qss)

    print()
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
