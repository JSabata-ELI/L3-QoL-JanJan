"""Master eye in the PV list's header (Image Slider and Image Finder share the table).

Claims:
  1. One click hides every PV (the shot counter row too).
  2. The next click brings back exactly the ones that were shown before.
  3. A double-click shows all of them — and its first click never hides
     everything for a moment.
  4. The header glyph is 👁 while anything is shown, 🚫 when nothing is.
  5. The Image Slider's host keeps the eye of PVs that are not listed right now.

Runs headless:  python testing/test_pv_master_eye.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="it_eye_")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtTest import QTest                            # noqa: E402
from PySide6.QtWidgets import QApplication                  # noqa: E402

import is_t                                                 # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILURES.append(name)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    names = ["PV_A", "PV_B", "PV_C", is_t.SHOT_ROW_KEY]
    state = {"hidden": {"PV_B"}, "emits": 0}
    tbl = is_t.PvValueTable()

    def redraw():
        tbl.refresh(names, state["hidden"], lambda n: ("1", False, ""))

    def host(hidden):
        state["emits"] += 1
        state["hidden"] = set(hidden)
        redraw()
    tbl.hidden_set_requested.connect(host)
    redraw()
    wait = QApplication.doubleClickInterval() + 80
    hdr = tbl.horizontalHeader()

    def glyph():
        return tbl.horizontalHeaderItem(0).text()

    check("header shows the eye", glyph() == "👁")

    print("Click: hide all")
    hdr.sectionClicked.emit(0)
    check("nothing happens before the double-click time is up", state["emits"] == 0)
    QTest.qWait(wait)
    check("everything hidden", state["hidden"] == set(names), str(state["hidden"]))
    check("header shows 🚫", glyph() == "🚫")

    print("Click again: back to what was shown")
    hdr.sectionClicked.emit(0)
    QTest.qWait(wait)
    check("only PV_B still hidden", state["hidden"] == {"PV_B"}, str(state["hidden"]))
    check("header shows the eye again", glyph() == "👁")

    print("Double-click: show all")
    state["emits"] = 0
    hdr.sectionClicked.emit(0)
    hdr.sectionClicked.emit(0)
    QTest.qWait(wait)
    check("one request only", state["emits"] == 1, str(state["emits"]))
    check("all shown", state["hidden"] == set(), str(state["hidden"]))

    print("Click with nothing remembered while all are hidden → show all")
    state["hidden"] = set(names)
    redraw()
    tbl._eye_memory = None
    hdr.sectionClicked.emit(0)
    QTest.qWait(wait)
    check("all shown", state["hidden"] == set(), str(state["hidden"]))

    print("Real mouse on the header")
    from PySide6.QtCore import QPoint, Qt
    tbl.resize(300, 200)
    hdr.resize(300, hdr.sizeHint().height())
    at = QPoint(hdr.sectionViewportPosition(0) + hdr.sectionSize(0) // 2,
                hdr.height() // 2)
    QTest.mouseClick(hdr.viewport(), Qt.MouseButton.LeftButton, pos=at)
    QTest.qWait(wait)
    check("real click hides all", state["hidden"] == set(names), str(state["hidden"]))
    state["emits"] = 0
    QTest.mouseClick(hdr.viewport(), Qt.MouseButton.LeftButton, pos=at)
    QTest.mouseDClick(hdr.viewport(), Qt.MouseButton.LeftButton, pos=at)
    QTest.qWait(wait)
    check("real double-click shows all, one request", state["hidden"] == set()
          and state["emits"] == 1, f"{state['hidden']} / {state['emits']}")

    print("Other columns do nothing")
    state["emits"] = 0
    hdr.sectionClicked.emit(1)
    QTest.qWait(wait)
    check("no request", state["emits"] == 0)

    print("Image Slider host")

    class _Host:
        _pv_set_hidden = is_t.Viewer._pv_set_hidden

        def __init__(self):
            self._pv_enabled = ["PV_A", "PV_C"]
            self._pv_hidden = {"PV_OLD"}          # not listed now
            self._shot_hidden = False
            self._ui_state = {}

        def _save_ui_state(self):
            pass

        def _pv_rebuild_table(self):
            pass

        def _pv_update_overlay(self):
            pass
    h = _Host()
    h._pv_set_hidden({"PV_A", "PV_C", is_t.SHOT_ROW_KEY})
    check("listed PVs hidden, unlisted eye kept",
          h._pv_hidden == {"PV_A", "PV_C", "PV_OLD"}, str(h._pv_hidden))
    check("shot counter hidden", h._shot_hidden is True)
    h._pv_set_hidden(set())
    check("show all keeps the unlisted one", h._pv_hidden == {"PV_OLD"}
          and h._shot_hidden is False)
    check("saved to ui state", h._ui_state.get("pv_hidden") == ["PV_OLD"])

    print()
    print("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
