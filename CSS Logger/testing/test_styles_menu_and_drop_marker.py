"""
Styles menu (above the PV table) and the column drop marker.

  * Styles → Delete → <look> asks its yes/no question only AFTER the menu has
    closed (a box opened under an open menu cannot be answered), and removes
    the look on Yes;
  * with no saved look, Load and Delete are greyed out and say why;
  * a menu entry under the mouse gets a light-blue background, greyed-out text
    is grey;
  * dragging a column heading shows a blue upright line on the edge where the
    column will land, and the line goes away on release.

Runs headless, no archiver needed, nothing is written to the settings file.

Run:  python test_styles_menu_and_drop_marker.py   (prints PASS/FAIL, exits non-zero)
"""
import os
import pathlib
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

_HERE = pathlib.Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import main as app_main                                              # noqa: E402
from PySide6.QtCore import Qt, QEvent, QPointF                       # noqa: E402
from PySide6.QtGui import QMouseEvent, QColor                        # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)
app.setStyleSheet(app_main._APP_STYLESHEET)
app_main.CSSLoggerWidget._maybe_autostart_live = lambda self: None
w = app_main.CSSLoggerWidget()
w._request_reload = lambda *a, **k: None
w._save_runtime_state = lambda *a, **k: None                  # keep the real settings untouched

failures = []


def check(what, ok):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        failures.append(what)


def submenu(menu, title):
    for act in menu.actions():
        if act.menu() is not None and act.text() == title:
            return act.menu()
    return None


# ── Delete runs after the menu closes ──────────────────────────────────────
asked = []
QMessageBox.question = staticmethod(
    lambda *a, **k: (asked.append(a), QMessageBox.StandardButton.Yes)[1])

w._style_presets = {"Look A": {"pv_settings": {}}, "Look B": {"pv_settings": {}}}
menu_owner = QToolButton()
menu = w._build_styles_menu(menu_owner)
w._fill_styles_menu(menu)
dmenu = submenu(menu, "Delete")
check("Delete submenu is enabled with saved looks", dmenu is not None and dmenu.isEnabled())
act = next(a for a in dmenu.actions() if a.text() == "Look A")
act.trigger()
check("no question asked while the menu is still open", not asked)
app.processEvents()
check("question asked once the menu has gone", len(asked) == 1)
check("look removed on Yes", "Look A" not in w._style_presets and "Look B" in w._style_presets)
check("status line names the deleted look", "Look A" in w._lbl_status.text())

# ── Empty: greyed out with a reason ───────────────────────────────────────
w._style_presets = {}
w._fill_styles_menu(menu)
for title in ("Load", "Delete"):
    sm = submenu(menu, title)
    check(f"{title} greyed out with no saved look", sm is not None and not sm.isEnabled())
    check(f"{title} says why", sm is not None and "No saved looks" in sm.menuAction().toolTip())

# ── Menu colours ───────────────────────────────────────────────────────────
w._style_presets = {"Look B": {"pv_settings": {}}}
w._fill_styles_menu(menu)
menu.move(-3000, -3000)                       # never on the visible desktop
menu.show()
first = menu.actions()[0]
menu.setActiveAction(first)
app.processEvents()
img = menu.grab().toImage()
r = menu.actionGeometry(first)
bg = QColor(img.pixel(r.right() - 4, r.center().y()))
check(f"hovered entry is light blue (got {bg.name()})",
      abs(bg.red() - 0xDC) < 12 and abs(bg.green() - 0xEB) < 12 and bg.blue() > 0xF0)
other = menu.actions()[1]
r2 = menu.actionGeometry(other)
bg2 = QColor(img.pixel(r2.right() - 4, r2.center().y()))
check(f"other entries stay white (got {bg2.name()})", bg2.lightness() > 245)
menu.hide()

# ── Drop marker while dragging a heading ──────────────────────────────────
w.resize(1600, 900)
w.move(-4000, -4000)
w.show()
app.processEvents()
tv  = w._axis_tv
hdr = tv.horizontalHeader()
vp  = hdr.viewport()
line = w._axis_drop_marker._line

visible = [hdr.logicalIndex(v) for v in range(hdr.count())
           if not hdr.isSectionHidden(hdr.logicalIndex(v))]
src, dst = visible[1], visible[4]
y = hdr.height() // 2


def mouse(kind, x, buttons):
    ev = QMouseEvent(kind, QPointF(x, y), QPointF(vp.mapToGlobal(QPointF(x, y))),
                     Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(vp, ev)


x_src = hdr.sectionViewportPosition(src) + hdr.sectionSize(src) // 2
# Past the middle of the target column → Qt lands the column there, after it.
x_dst = hdr.sectionViewportPosition(dst) + hdr.sectionSize(dst) * 3 // 4
mouse(QEvent.Type.MouseButtonPress, x_src, Qt.MouseButton.LeftButton)
mouse(QEvent.Type.MouseMove, x_src + 20, Qt.MouseButton.LeftButton)
mouse(QEvent.Type.MouseMove, x_dst, Qt.MouseButton.LeftButton)
app.processEvents()
edge = hdr.x() + hdr.sectionViewportPosition(dst) + hdr.sectionSize(dst)
check("marker shows while dragging", line.isVisible())
check(f"marker on the right edge of the target column (line {line.x()}, edge {edge})",
      abs(line.geometry().center().x() - edge) <= 2)
check("marker spans the header and the rows",
      line.height() >= tv.height() - hdr.y() - 1)
mouse(QEvent.Type.MouseButtonRelease, x_dst, Qt.MouseButton.NoButton)
app.processEvents()
check("marker gone after release", not line.isVisible())
check("column really moved to the marked place", hdr.visualIndex(src) == hdr.visualIndex(dst) + 1
      or hdr.visualIndex(src) > hdr.visualIndex(dst))

w.hide()
print("PASS" if not failures else f"FAIL ({len(failures)})")
sys.exit(1 if failures else 0)
