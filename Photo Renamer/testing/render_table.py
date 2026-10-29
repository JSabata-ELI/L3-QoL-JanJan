"""
render_table.py  --  screenshots of the Table tab and the table it saves, for
the legibility check.

Rebuilds a 3 x 6 near-field comparison like the hand-made ones (multi-line
column headings, camera names down the left) from generated pictures, then
writes table_tab.png (the tab), table_tab_small.png (the window at its
smallest) and table.png (the saved picture, full size).

Runs on the minimal platform with the real fonts: nothing appears on screen.

    python testing/render_table.py <output folder>
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "minimal:enable_fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtWidgets import QApplication                      # noqa: E402

import pr_t                                                      # noqa: E402
import main as main_mod                                          # noqa: E402
from test_table import _picture                                  # noqa: E402

HEADS = ["WP 360k\nBoth heads ON\n0,2 Hz\nHP filters",
         "WP 800k\nBoth heads ON\nAll PCs\nHP filters",
         "WP 600k\nHead 1 ON\nAll PCs\nLP filters",
         "WP 600k\nHead 1 ON\nJust PC1\nLP filters",
         "WP 600k\nHead 2 ON\nAll PCs\nLP filters",
         "WP 600k\nHead 2 ON\nJust PC2\nLP filters"]
ROWS = ["C03-079-PCM2NF", "C03-099-PTM12wNF", "C03-035-PTM11wNF"]
COLOURS = [("#3a0a05", "#ff7a00"), ("#7a1000", "#ffe000"), ("#ff4000", "#40e0ff")]


def _settle(times: int = 40) -> None:
    app = QApplication.instance()
    for _ in range(times):
        app.processEvents()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    pr_t.load_ui_state = lambda: {}
    pr_t.save_ui_state = lambda state: None
    pr_t._remember = lambda key, value: None
    main_mod.load_ui_state = lambda: {}
    main_mod.save_ui_state = lambda state: None

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QWidget      { background: #f3f3f3; color: #111; }
        QLabel       { background: transparent; }
        QPushButton  { padding: 5px 8px; }
    """)
    tmp = Path(tempfile.mkdtemp(prefix="pr_table_render_"))
    try:
        paths = []
        for r in range(3):
            for c in range(6):
                p = tmp / f"cam{r + 1}_set{c + 1}.png"
                a, b = COLOURS[r]
                _picture(1280, 1024, a, b).save(str(p))
                paths.append(str(p))
        win = main_mod.PhotoRenamerWindow()
        win.resize(1320, 860)
        win.show()
        w = win.centralWidget()
        w.open_folder(tmp)
        tab = w._table
        import tb_t
        tab.spec = tb_t.TableSpec(rows=3, cols=6, col_labels=list(HEADS),
                                  row_labels=list(ROWS), corner="",
                                  notes="Measured 21.10.2026 after the LP"
                                        " filter change. Same exposure for"
                                        " every camera.")
        tab.spec.place(paths)
        tab._after_shape_change()
        w._tabs.setCurrentIndex(1)
        _settle(60)
        tab._pool.waitForDone(20000)
        _settle(60)
        tab._refresh_preview()
        _settle(10)
        win.grab().save(str(out / "table_tab.png"))
        img = tab.build_full_image()
        if img is not None:
            img.save(str(out / "table.png"))
            print(f"  table.png {img.width()} x {img.height()}")
        win.resize(win.minimumSizeHint().width(), win.minimumHeight())
        _settle(30)
        print(f"  smallest window {win.width()} x {win.height()}")
        win.grab().save(str(out / "table_tab_small.png"))
        w._stop_loading()
        tab.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
