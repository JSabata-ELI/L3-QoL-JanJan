"""Render the tile captions (27.10.2026: right under the picture, "Label size").

Runs on the `minimal` platform with real fonts — nothing appears on screen and no
window takes the keyboard focus:

    python testing/render_caption_size.py [output folder]

Writes cap_<mode>_<pt>.png for the grid / rows / cols walls at 8.5 and 16 pt,
cap_saved_16.png (the "Save view" picture) and cap_panel.png (the panel row).
"""
import os
import sys
from datetime import timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "minimal:enable_fonts"

from test_finder_moment import load_finder                    # noqa: E402
from render_day_wall_vertical import CAMS, DAY0, ns_at, frame  # noqa: E402

PANE_W, PANE_H = 1240, 720


def make_wall(m, app, mode: str, pt: float, n_cams: int, n_days: int):
    shared = m._WallShared()
    shared.cap_pt = pt
    wall = m._DayWall(shared=shared)
    wall.set_layout_mode(mode)
    cells = []
    for d in range(n_days):
        day = DAY0 + timedelta(days=d)
        for c, cam in enumerate(CAMS[:n_cams]):
            p = Path(f"cap_{mode}_{d}_{c}.png")
            shared.raw[p] = frame(d * 5 + c)
            cells.append({"day": day, "cam": cam, "cam_folder": cam + "-_-IMG",
                          "path": p, "ts_ns": ns_at(day, 9 + c, 17 * d % 60),
                          "status": "found", "meta": {"source": "pv"}})
    wall.set_cells(cells)
    sc = m._WallScroll()
    sc.setWidgetResizable(True)
    sc.setWidget(wall)
    wall._scroll_host = sc
    sc.resize(PANE_W, PANE_H)
    sc.show()
    app.processEvents()
    wall._relayout()
    app.processEvents()
    return wall, sc


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent
    m = load_finder()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")

    for mode, n_cams, n_days in (("grid", 1, 5), ("rows", 2, 4), ("cols", 2, 4)):
        for pt in (8.5, 16.0):
            wall, sc = make_wall(m, app, mode, pt, n_cams, n_days)
            out = out_dir / f"cap_{mode}_{pt:g}.png"
            sc.grab().save(str(out))
            print("written:", out.name, "caption strip", wall._CAPTION_H, "px")
            if mode == "grid" and pt == 16.0:
                img = wall.composite_image(scale=2.0)
                img.save(str(out_dir / "cap_saved_16.png"))
                print("written: cap_saved_16.png", img.width(), "x", img.height())
            sc.hide()

    # Never touch the operator's own saved settings.
    import tempfile
    m.ImageFinderWidget._PV_STATE_PATH = (
        Path(tempfile.mkdtemp(prefix="if_cap_")) / "finder_ui_state.json")
    w = m.ImageFinderWidget()
    w.resize(1500, 950)
    w.show()
    app.processEvents()
    w._cap_slider.setValue(28)
    app.processEvents()
    w._cap_slider.parentWidget().grab().save(str(out_dir / "cap_panel.png"))
    print("written: cap_panel.png, label says", w._cap_val_lbl.text(),
          "shared", w._wall_shared.cap_pt)
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
