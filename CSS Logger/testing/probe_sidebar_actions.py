"""How the Spectra sidebar reads now that Mode is gone.

Three things can only be judged from a render:
  * the top row — the Live switch (green while it runs, red while it does not)
    next to Stop, with no "Idle" pill any more;
  * the action row under it — Analyze and Export on one line;
  * the Selected spectra block, which must NOT swallow the whole panel when the
    list is empty.

Writes testing/_out/sidebar_*.png. Nothing is shown on screen — the widget is
grabbed, never shown.

    python testing/probe_sidebar_actions.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np                                   # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

import sp_t                                          # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

NX = 128
X = np.linspace(780.0, 840.0, NX)
T0 = int(1_756_000_000 * 1e9)
SEC = 10 ** 9


def _region(rid, times):
    rng = np.random.default_rng(rid)
    st = rng.random((len(times), NX))
    stats = sp_t._stats_from_stack(st)
    return {
        "id": rid, "t_start": min(times), "t_end": max(times),
        "color": "#1f77b4", "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True, "x": X,
        "stack_all": st, "stack_ts_all": times, "n_all": len(times),
        "stats_all": {k: stats[k] for k in sp_t.STAT_KEYS},
        "scalar_series": {}, "shot_vals": {},
        "orders_all": {}, "energy_avg_all": 9.87, "energy_n_all": len(times),
    }


def _settle(w):
    """Force a real layout pass. A widget whose top level was never shown keeps
    its build-time geometry, so the first measurement is otherwise nonsense."""
    from PySide6.QtWidgets import QWidget
    for _ in range(4):
        for child in w.findChildren(QWidget):
            child.updateGeometry()
        w.updateGeometry()
        lay = w.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        w.adjustSize()
        QApplication.processEvents()


def _grab(w, name):
    _settle(w)
    pm = w.grab()
    path = os.path.join(OUT, name)
    pm.save(path)
    print(f"  wrote {os.path.basename(path)}   {pm.width()}x{pm.height()} px")


def _geom(w, tag):
    """Where each block actually ended up, in sidebar coordinates."""
    sb = w._sidebar_scroll.widget()
    _settle(sb)
    print(f"[{tag}]  sidebar {sb.width()}x{sb.height()} px")
    for label, widget in (("Live",        w._btn_live),
                          ("Stop",        w._btn_stop),
                          ("Analyze",     w._btn_analyze),
                          ("Export",      w._btn_export),
                          ("Clear all",   w._btn_clear_regs),
                          ("regions box", w._regions_w),
                          ("Shot filter", w._g_filter),
                          ("Average N",   w._g_live),
                          ("Display set", w._g_settings)):
        # isVisible() is False for everything while the top level was never
        # shown — isVisibleTo() is the one that answers "would it be on screen".
        shown = widget.isVisibleTo(sb)
        p = widget.mapTo(sb, widget.rect().topLeft())
        print(f"    {label:12s}  y {p.y():4d}..{p.y() + widget.height():4d}"
              f"   w {widget.width():3d}"
              + ("" if shown else "   HIDDEN")
              + ("" if widget.isEnabled() else "   disabled"))


def _live_colour(w):
    """The Live switch must be green while live and red while not — read it
    back off the stylesheet, which is the only thing that paints it."""
    css = w._btn_live.styleSheet()
    green = "#d9f2d9" in css
    red = "#f9dedb" in css
    print(f"    Live button: live={w._live}  green={green}  red={red}  "
          + ("OK" if green != red and green == w._live else "WRONG"))


def main():
    app = QApplication.instance() or QApplication([])
    # The tab never runs bare: main.py paints every widget light. Without it the
    # render comes out on the Windows dark theme and every judgement about
    # legibility is about a window that does not exist.
    try:
        import main as _main
        app.setStyleSheet(_main._APP_STYLESHEET)
    except Exception as exc:
        print(f"  (no app stylesheet: {exc})")
    w = sp_t.SpectraWidget()
    w.resize(1500, 1400)
    w._x_data = X

    # ── empty list: this is the state that used to look enormous ──────
    w._rebuild_regions_ui()
    w._update_action_buttons()
    QApplication.processEvents()
    _geom(w, "empty list")
    _live_colour(w)
    _grab(w._sidebar_scroll.widget(), "sidebar_empty.png")

    # ── three selections ──────────────────────────────────────────────
    w._regions = [_region(i, [T0 + i * 100 * SEC + k * SEC for k in range(50)])
                  for i in range(1, 4)]
    w._rebuild_regions_ui()
    w._update_action_buttons()
    QApplication.processEvents()
    _geom(w, "three selections")
    _grab(w._sidebar_scroll.widget(), "sidebar_three.png")

    # ── live running ──────────────────────────────────────────────────
    w._live = True
    w._g_live.setVisible(True)
    w._btn_live.setChecked(True)
    w._refresh_pill()
    QApplication.processEvents()
    _geom(w, "live running")
    _live_colour(w)
    _grab(w._sidebar_scroll.widget(), "sidebar_live.png")

    w._live = False
    w._btn_live.setChecked(False)
    w._refresh_pill()
    _live_colour(w)

    w.cancel_scan()


if __name__ == "__main__":
    main()
