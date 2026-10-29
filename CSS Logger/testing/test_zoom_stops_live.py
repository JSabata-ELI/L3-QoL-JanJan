"""A zoom in the graph ends Live mode; Home brings it back.

Asked for on 26.10.2026: zooming in while Live runs used to freeze only the
view while new data kept arriving underneath. Now:

1. A right-drag zoom switches Live off and keeps the zoomed range.
2. A toolbar Zoom / Pan drag that moves the range switches Live off; pressing
   the tool in without dragging does not.
3. Home switches Live back on, but only when a zoom switched it off.

No network: Live is set up by hand and starting it again is recorded, not run.
Headless (offscreen), no window takes the focus:

    python testing/test_zoom_stops_live.py
"""
import os
import sys
from datetime import timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

import test_smoke as smoke                      # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def _pretend_live(w):
    """Live as it looks after its first load, without asking any archiver."""
    w._live_mode = True
    w._live_window_span = timedelta(hours=1)
    w._btn_live.setText("⏹ Stop live")
    starts = []
    w._toggle_live_mode = lambda: starts.append(1)
    return starts


def _narrow(w):
    lo, hi = w._graph_axes[0].get_xlim()
    return lo + 0.4 * (hi - lo), lo + 0.6 * (hi - lo)


def main() -> int:
    print("test_zoom_stops_live")
    w = smoke._loaded_widget()
    app = smoke.QApplication.instance()

    # 1. Right-drag zoom.
    starts = _pretend_live(w)
    a, b = _narrow(w)
    w._on_zoom_select(a, b)
    lo, hi = w._graph_axes[0].get_xlim()
    check("right-drag zoom stops Live", not w._live_mode)
    check("the Live button offers to start again",
          w._btn_live.text() == "⏵ Live mode", w._btn_live.text())
    check("the zoomed range stays", abs(lo - a) < 1e-9 and abs(hi - b) < 1e-9)
    check("the zoom is remembered as the reason", w._live_stopped_by_zoom)
    w._on_graph_view_home()
    check("Home starts Live again after a zoom stop", starts == [1])
    check("Home clears the reason", not w._live_stopped_by_zoom)

    # 2. Toolbar Zoom pressed in, no drag: Live keeps running.
    starts = _pretend_live(w)
    tb = w._graph_toolbar
    tb.zoom()
    w._on_graph_tb_press(None)
    w._on_graph_tb_release(None)
    app.processEvents()
    check("pressing the magnifier in alone keeps Live", w._live_mode)

    # …a drag that moves the range stops it.
    w._on_graph_tb_press(None)
    a, b = _narrow(w)
    w._graph_axes[0].set_xlim(a, b)
    w._on_graph_tb_release(None)
    app.processEvents()
    check("a toolbar zoom drag stops Live", not w._live_mode)
    check("…and keeps its range",
          abs(w._graph_axes[0].get_xlim()[0] - a) < 1e-9)
    tb.zoom()                                    # let go of the magnifier
    app.processEvents()

    # 3. Live stopped another way: Home does not start it.
    starts = _pretend_live(w)
    w._stop_live("stopped by the user")
    w._on_graph_view_home()
    check("Home leaves Live off when no zoom stopped it", starts == [])

    w.close()
    print("FAILED: " + "; ".join(FAILURES) if FAILURES else "ALL PASS")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
