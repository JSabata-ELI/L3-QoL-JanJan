"""Render the main window's tab row with the Image Slider's layout tabs (same
shape as the program tabs, smaller and paler) so the look can be judged from a
picture, not guessed.

    set QT_QPA_PLATFORM=windows
    python testing/render_session_chips.py

Builds the real main window (build_main_window) with a throw-away %APPDATA%, so
no saved layout tab is restored. The window is shown without taking the keyboard
focus. Writes testing/session_chips.png and checks:
  - the layout tabs stand on the main tabs' bottom line (not a row lower);
  - every layout tab's close button carries the dark "x".
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="it_chips_")

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from PySide6.QtCore import Qt, QRect, QPoint            # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget, QTabBar, QFrame  # noqa: E402

APP_QSS = """
    QWidget      { background: #f3f3f3; color: #111; }
    QLabel       { background: transparent; }
    QPushButton  { padding: 5px 8px; }
    QTabWidget::pane { border: 1px solid #ccc; }
    QTabBar::tab {
        background: #e8e8e8; color: #444;
        padding: 6px 18px; border: 1px solid #ccc;
        border-bottom: none; border-radius: 3px 3px 0 0;
        margin-right: 2px;
    }
    QTabBar::tab:selected { background: #f3f3f3; color: #111; font-weight: 600; }
    QTabBar::tab:hover    { background: #d8e8ff; }
"""


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    import main as it_main
    win = it_main.build_main_window(None)
    # The real first start opens the Time window dialog — not wanted here.
    sys.modules["image_slider"].Viewer.auto_start_online = lambda self: None

    win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    win.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    win.setMinimumSize(800, 600)
    win.resize(1500, 700)
    win.show()
    app.processEvents()

    tabs = win.centralWidget()
    assert isinstance(tabs, QTabWidget)
    slider = next(tabs.widget(i) for i in range(tabs.count())
                  if tabs.tabText(i) == "Image Slider")
    tabs.setCurrentWidget(slider)
    slider.new_tab("Ghost tiles")
    slider.new_tab("Laser running")
    slider._tabs.setCurrentIndex(1)
    for _ in range(30):
        app.processEvents()

    fails = []

    def check(cond, what):
        print(("ok   " if cond else "FAIL ") + what)
        if not cond:
            fails.append(what)

    tray = win.findChild(QFrame, "sessionTray")
    check(tray is not None and tray.isVisible(), "layout tabs shown on the Image Slider tab")
    main_bar = tabs.tabBar()
    sbar = slider._tabs.tabBar()
    main_bottom = main_bar.mapTo(win, main_bar.tabRect(0).bottomLeft()).y()
    s_bottom = sbar.mapTo(win, sbar.tabRect(0).bottomLeft()).y()
    print(f"main tab bottom y={main_bottom}, layout tab bottom y={s_bottom}, "
          f"layout tab height={sbar.tabRect(0).height()}, "
          f"main tab height={main_bar.tabRect(0).height()}")
    check(abs(main_bottom - s_bottom) <= 3, "layout tabs on the main tabs' bottom line")

    for i in range(sbar.count()):
        b = sbar.tabButton(i, QTabBar.ButtonPosition.RightSide)
        check(b is not None and b.icon().cacheKey() == slider._close_icon.cacheKey(),
              f"layout tab {i}: dark close x")

    h = max(main_bar.mapTo(win, QPoint(0, main_bar.height())).y(),
            tray.mapTo(win, QPoint(0, tray.height())).y()) + 8
    out = Path(__file__).with_name("session_chips.png")
    win.grab(QRect(0, 0, win.width(), h)).save(str(out))
    print("saved", out)

    win.close()
    print("\n" + ("ALL OK" if not fails else f"{len(fails)} FAILED"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
