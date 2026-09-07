"""Spectra tab — how the shot bar actually LOOKS, measured off a render.

Renders the search-graph panel with a trace, a title, two shaded selections and
the bar under it, then reads the pixels back:

  * the groove and the handle really are the colours the stylesheet asks for, and
    the handle is clearly darker than the groove it sits on,
  * the ◀ ▶ arrows are dark ink, not a pale glyph on a pale button,
  * the marker's tag does not sit on top of the graph's title,
  * the groove's ends line up with the plot box's ends.

Writes testing/_out/shot_bar_look.png and prints the numbers.

Run:  python testing/probe_shot_bar_look.py
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

import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

import sp_t
from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

NX = 128
X = np.linspace(780.0, 840.0, NX)
T0 = int(1_756_000_000 * 1e9)
SEC = 10 ** 9
HOUR = 3600 * SEC

_failed = False


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


def _region(rid, colour, times):
    n = len(times)
    st = np.exp(-0.5 * ((X[None, :] - 806.0) / 8.0) ** 2) * np.ones((n, 1))
    return {
        "id": rid, "t_start": min(times), "t_end": max(times),
        "color": colour, "visible": True, "expanded": False,
        "show_individual": False, "analyzed": True,
        "x": X, "stack": st, "stack_ts": list(times),
        "mean": st.mean(axis=0), "median": np.median(st, axis=0),
        "trimmed": st.mean(axis=0), "sigma": st.mean(axis=0),
        "std": st.std(axis=0),
        "p10": np.percentile(st, 10, axis=0), "p90": np.percentile(st, 90, axis=0),
        "orders": {"GDD": 1234.0, "TOD": -5678.0, "FOD": 9.0},
        "energy_avg": 9.87, "energy_n": n, "n": n,
    }


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    w = SpectraWidget()
    w._x_data = X
    w._x_axis_cfg = {"mode": "native"}
    w._sb_x_min.setValue(780)
    w._sb_x_max.setValue(840)
    w._chk_autofit.setChecked(False)
    w.resize(1500, 900)
    w.show()
    QApplication.processEvents()

    t1 = [T0 + 600 * SEC + i * 60 * SEC for i in range(30)]
    t2 = [T0 + 4000 * SEC + i * 60 * SEC for i in range(20)]
    win = [(T0, T0 + 2 * HOUR)]
    w._windows = win
    w._tmap = sp_t._TimeMap(win)
    w._regions = [_region(1, "#1565C0", t1), _region(2, "#C62828", t2)]
    w._region_seq = 2
    w._rebuild_regions_ui()
    w._cmb_method.setCurrentIndex(w._cmb_method.findText("Every spectrum"))
    w._redraw_spectra()

    # A trace and the real title on the search graph, so the tag has something to
    # collide with. _draw_energy needs archive data; this is the same picture.
    ax = w._ax_top
    ax.clear()
    ax._sp_time_axis = True
    tx = np.linspace(0.0, 7200.0, 1200)
    ax.plot(tx, 9.0 + 0.4 * np.sin(tx / 300.0), lw=0.9, color="#1565C0")
    ax.set_title("Drag to select time region(s), then click Analyze",
                 fontsize=10, color="#333")
    ax.set_xlim(*w._tmap.xlim())
    w._install_top_cursor_artists()
    w._paint_region_spans(ax)
    for _ in range(4):
        w._canvas_top.draw()
        QApplication.processEvents()

    w._go_to_shot(20)
    QApplication.processEvents()

    img = w._top_container.grab().toImage()
    path = os.path.join(OUT, "shot_bar_look.png")
    img.save(path)
    print("  rendered", path, f"{img.width()}x{img.height()}")

    sl = w._sl_shot
    org = sl.mapTo(w._top_container, QPoint(0, 0))
    mid_y = org.y() + sl.height() // 2
    gx, travel, hwid = sp_t._slider_metrics(sl)
    handle_cx = org.x() + int(round(sl.pixel_at_value(sl.value())))

    def px(x, y):
        c = img.pixelColor(int(x), int(y))
        return (c.red(), c.green(), c.blue())

    def lum(c):
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    handle = px(handle_cx, mid_y)
    # A groove sample well away from the handle, still inside the groove.
    groove_x = org.x() + gx + travel - 20
    groove = px(groove_x, mid_y)
    print(f"  handle at x={handle_cx} rgb={handle}   groove at x={groove_x} rgb={groove}")
    _ok(lum(handle) < 140, "the handle is dark ink", f"luma {lum(handle):.0f}")
    _ok(lum(groove) > 200, "on a light groove", f"luma {lum(groove):.0f}")
    _ok(lum(groove) - lum(handle) > 60, "and the two are plainly different",
        f"{lum(groove) - lum(handle):.0f} luma apart")

    # The ◀ button: the darkest pixel inside it must be real ink.
    b = w._btn_single_prev
    bo = b.mapTo(w._top_container, QPoint(0, 0))
    dark = min(lum(px(bo.x() + dx, bo.y() + dy))
               for dx in range(4, b.width() - 4) for dy in range(4, b.height() - 4))
    _ok(dark < 110, "the ◀ arrow is dark ink on a light button", f"darkest {dark:.0f}")

    # The groove's ends against the plot box's ends.
    ratio = getattr(w._canvas_top, "device_pixel_ratio", 1) or 1
    bb = ax.get_window_extent()
    cvo = w._canvas_top.mapTo(w._top_container, QPoint(0, 0))
    box_l = cvo.x() + bb.x0 / ratio
    box_r = cvo.x() + bb.x1 / ratio
    reach_l = org.x() + sl.pixel_at_value(0)
    reach_r = org.x() + sl.pixel_at_value(sp_t._SHOT_BAR_MAX)
    print(f"  plot box {box_l:.1f}..{box_r:.1f}   bar reaches {reach_l:.1f}..{reach_r:.1f}")
    _ok(abs(reach_l - box_l) <= 1.0 and abs(reach_r - box_r) <= 1.0,
        "the bar's travel covers exactly the plot box",
        f"{reach_l - box_l:+.1f} / {reach_r - box_r:+.1f} px")

    # The tag must be below the title, not over it.
    tag = w._top_marker["tag"]
    ttl = ax.title
    rend = w._canvas_top.get_renderer()
    tag_top = tag.get_window_extent(rend).y1
    ttl_bot = ttl.get_window_extent(rend).y0
    _ok(tag_top <= ttl_bot + 1, "the marker's tag stays clear of the graph title",
        f"tag top {tag_top:.0f} vs title bottom {ttl_bot:.0f} (y up)")

    w.close()
    w.deleteLater()
    print("\nRESULT:", "FAILED" if _failed else "OK")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
