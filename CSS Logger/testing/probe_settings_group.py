"""Spectra tab — the side panel's order and the new settings group, off a render.

Checks, on a real (non-offscreen) render of the whole side panel:

  * the blocks stand in the order the panel is meant to be used:
    day & time -> search by -> spectrum -> selected spectra -> mode ->
    Analyze -> Clear all / Export -> Display settings,
  * the graph options, the comparison curve and the From/To range all live
    INSIDE the Display settings group and nowhere else,
  * that group's header is dark enough for white text, its body pale enough for
    black text, and every label / control inside it is legible,
  * the range caption follows the unit: "Wavelength range [nm]" on a
    spectrometer, "Time range [fs]" on the SPIDER time domain.

Writes testing/_out/settings_group.png and prints the numbers.

Run:  python testing/probe_settings_group.py
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

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

import sp_t
from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

_failed = False


def _ok(cond, msg, extra=""):
    global _failed
    print(("  PASS  " if cond else "  FAIL  ") + msg + (f"   {extra}" if extra else ""))
    if not cond:
        _failed = True


def _lum(c):
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    # The same style the program starts with — without it the render shows the
    # inherited dark theme and every combo box looks unreadable for no reason.
    app.setStyle("Fusion")
    app.setStyleSheet(sp_t._APP_STYLESHEET)
    w = SpectraWidget()
    w.resize(1500, 950)
    w.show()
    for _ in range(3):
        QApplication.processEvents()

    panel = w._sidebar_scroll.widget()

    def top_of(widget):
        return widget.mapTo(panel, QPoint(0, 0)).y()

    # ── 1. the order down the panel ───────────────────────────────────────
    order = [
        ("1 Day & time",       w._btn_pick_day),
        ("2 Search by",        w._tbl_pvs),
        ("3 Spectrum",         w._btn_spec),
        ("Selected spectra",   w._regions_w),
        ("Mode",               w._g_mode),
        ("Analyze",            w._btn_analyze),
        ("Clear all",          w._btn_clear_regs),
        ("Export",             w._btn_export),
        ("Display settings",   w._g_settings),
    ]
    ys = [(name, top_of(wid)) for name, wid in order]
    print("  order (y of each block):")
    for name, y in ys:
        print(f"    {y:5d}  {name}")
    for (n1, y1), (n2, y2) in zip(ys, ys[1:]):
        _ok(y1 <= y2, f"{n1} is above {n2}", f"{y1} vs {y2}")

    # ── 2. what is inside the settings group ──────────────────────────────
    body = w._g_settings.body
    inside = [
        ("Show",             w._cmb_method),
        ("Colour",           w._cmb_color),
        ("Normalize",        w._cmb_norm),
        ("Variation band",   w._chk_std),
        ("Smooth",           w._chk_smooth),
        ("Show search graph", w._chk_show_energy),
        ("range From",       w._sb_x_min),
        ("range To",         w._sb_x_max),
        ("Auto-fit",         w._chk_autofit),
        ("comparison curve", w._chk_compare),
        ("compare A",        w._cmb_cmp_a),
        ("compare mode",     w._cmb_cmp_mode),
    ]
    for name, wid in inside:
        p, found = wid.parentWidget(), False
        while p is not None:
            if p is body:
                found = True
                break
            p = p.parentWidget()
        _ok(found, f"'{name}' is inside the Display settings group")

    # ── 3. the caption follows the unit ───────────────────────────────────
    w._edit_x_unit.setText("nm")
    w._on_x_unit_edited()
    cap_nm = w._lbl_xrange.text()
    w._edit_x_unit.setText("fs")
    w._on_x_unit_edited()
    cap_fs = w._lbl_xrange.text()
    print(f"  caption with nm: {cap_nm!r}      with fs: {cap_fs!r}")
    _ok("NM" in cap_nm.upper() and "WAVELENGTH" in cap_nm.upper(),
        "nm gives a wavelength range caption", cap_nm)
    _ok("FS" in cap_fs.upper() and "TIME" in cap_fs.upper(),
        "fs gives a time range caption", cap_fs)
    w._edit_x_unit.setText("nm")
    w._on_x_unit_edited()
    QApplication.processEvents()

    # ── 4. the render: colours and legibility ─────────────────────────────
    w._g_settings.parentWidget().adjustSize()
    for _ in range(3):
        QApplication.processEvents()
    img = panel.grab().toImage()
    path = os.path.join(OUT, "settings_group.png")
    img.save(path)
    print("  rendered", path, f"{img.width()}x{img.height()}")

    hdr = w._g_settings._header
    ho = hdr.mapTo(panel, QPoint(0, 0))
    hdr_bg = img.pixelColor(ho.x() + hdr.width() - 6, ho.y() + hdr.height() // 2)
    bo = body.mapTo(panel, QPoint(0, 0))
    body_bg = img.pixelColor(bo.x() + body.width() - 5, bo.y() + body.height() - 5)
    print(f"  header bg {hdr_bg.name()} luma {_lum(hdr_bg):.0f}   "
          f"body bg {body_bg.name()} luma {_lum(body_bg):.0f}")
    _ok(_lum(hdr_bg) < 140, "the header is dark, so its white title reads",
        f"luma {_lum(hdr_bg):.0f}")
    _ok(_lum(body_bg) > 205, "the body is pale, so black control text reads",
        f"luma {_lum(body_bg):.0f}")

    # The darkest pixel of each caption / label inside the body must be real ink.
    for name, wid in (("Graph caption", w._lbl_xrange),):
        o = wid.mapTo(panel, QPoint(0, 0))
        dark = 255.0
        for yy in range(o.y(), o.y() + wid.height()):
            for xx in range(o.x(), o.x() + wid.width()):
                dark = min(dark, _lum(img.pixelColor(xx, yy)))
        _ok(dark < 150, f"{name} is drawn in ink, not in the background",
            f"darkest luma {dark:.0f}")

    # ── 5. Mode says which mode is on ─────────────────────────────────────
    # Both buttons are drawn; the chosen one has to look different from the other,
    # and its own text has to stay readable on whatever it is filled with.
    def _mode_px(btn):
        o = btn.mapTo(panel, QPoint(0, 0))
        bg = img.pixelColor(o.x() + 6, o.y() + btn.height() // 2)
        lo, hi = 255.0, 0.0
        for yy in range(o.y() + 3, o.y() + btn.height() - 3):
            for xx in range(o.x() + 3, o.x() + btn.width() - 3):
                v = _lum(img.pixelColor(xx, yy))
                lo, hi = min(lo, v), max(hi, v)
        # The label may be dark ink on light or white on a dark fill — take
        # whichever of the two extremes is further from the background.
        ink = lo if abs(_lum(bg) - lo) >= abs(hi - _lum(bg)) else hi
        return bg, ink

    on_bg, on_ink = _mode_px(w._btn_archive)
    off_bg, off_ink = _mode_px(w._btn_live_mode)
    print(f"  Archive (on)  bg {on_bg.name()} luma {_lum(on_bg):.0f}    "
          f"Live (off) bg {off_bg.name()} luma {_lum(off_bg):.0f}")
    _ok(abs(_lum(on_bg) - _lum(off_bg)) > 60,
        "the chosen mode looks different from the other one",
        f"{_lum(on_bg):.0f} vs {_lum(off_bg):.0f}")
    _ok(abs(_lum(on_bg) - on_ink) > 60,
        "and its own label still reads on it",
        f"bg {_lum(on_bg):.0f} vs ink {on_ink:.0f}")

    # Nothing may hang out of the panel: the group is as wide as the panel and
    # every control inside it fits.
    _ok(bo.x() + body.width() <= panel.width(),
        "the settings body fits the panel width",
        f"{bo.x() + body.width()} <= {panel.width()}")
    for name, wid in inside:
        o = wid.mapTo(panel, QPoint(0, 0))
        _ok(o.x() + wid.width() <= panel.width(),
            f"'{name}' fits the panel width",
            f"{o.x() + wid.width()} <= {panel.width()}")

    # ── 6. Live mode puts its own controls right under Mode ───────────────
    w._btn_live_mode.click()
    for _ in range(3):
        QApplication.processEvents()
    _ok(w._g_live.isVisible(), "switching to Live shows the Live controls")
    _ok(top_of(w._g_mode) < top_of(w._g_live) < top_of(w._btn_analyze),
        "and they sit between Mode and Analyze",
        f"{top_of(w._g_mode)} < {top_of(w._g_live)} < {top_of(w._btn_analyze)}")
    _ok(top_of(w._g_settings) > top_of(w._g_live),
        "with the settings group still last")
    img2 = panel.grab().toImage()
    p2 = os.path.join(OUT, "settings_group_live.png")
    img2.save(p2)
    print("  rendered", p2, f"{img2.width()}x{img2.height()}")
    w._btn_archive.click()
    QApplication.processEvents()

    print("\n  " + ("SOMETHING FAILED" if _failed else "all checks passed"))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
