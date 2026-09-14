"""Measure and LOOK at the Workshop's left panel, under main.py's own stylesheet.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/render_workshop_panel.py

Offscreen has no fonts and lies about text width, so the width printed here would be
meaningless there; this uses the real windows platform.

What it prints:
  * the width the splitter actually gives the panel,
  * what the panel needs with EVERY section open, and which widget sets that,
  * every control that carries no tooltip.

Writes `workshop_panel.png` beside this file.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_wk_panel_"))
os.environ["APPDATA"] = str(_TMP)

import wk_t                                                        # noqa: E402
from PySide6.QtWidgets import (QApplication, QWidget, QPushButton,  # noqa: E402
                               QCheckBox, QComboBox, QSpinBox,
                               QDoubleSpinBox, QSlider, QToolButton,
                               QLineEdit, QRadioButton)


def app_stylesheet() -> str:
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8", errors="ignore")
    marker = 'app.setStyleSheet("""'
    a = src.find(marker)
    if a < 0:
        return ""
    a += len(marker)
    b = src.find('"""', a)
    return src[a:b] if b > a else ""


WATCHED = (QPushButton, QCheckBox, QComboBox, QSpinBox, QDoubleSpinBox,
           QSlider, QToolButton, QLineEdit, QRadioButton)

#  Left without a tooltip on purpose:
#    the three true-size samples appear only while a size is being dragged and are
#    gone again before any tooltip could show, and the picture area itself would put
#    a yellow box over the picture every time the mouse came to rest on it — what it
#    answers to is stated in the status line and in its own right-click menu.
EXEMPT = {"_StrokeZoomPopup", "_TextZoomPopup", "WorkshopCanvas"}


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    else:
        print("WARNING: no app stylesheet read")

    w = wk_t.WorkshopWidget()
    w.resize(1600, 1000)
    w.show()
    app.processEvents()

    bad: list = []
    panel = w._panel_layout.parentWidget()
    scroll = panel.parentWidget().parentWidget()      # panel -> viewport -> scroll area

    print("panel widget width :", panel.width())
    print("viewport width     :", scroll.viewport().width())
    print("scroll area width  :", scroll.width())
    print("scroll minimumWidth:", scroll.minimumWidth())
    print("vbar shown / width :", scroll.verticalScrollBar().isVisible(),
          scroll.verticalScrollBar().width())
    cut = panel.width() > scroll.viewport().width()
    print("CUT OFF            :", "yes" if cut else "no")
    if cut:
        bad.append("the panel is wider than the room it is given")
    print("panel sizeHint     :", panel.sizeHint().width())
    print("panel minSizeHint  :", panel.minimumSizeHint().width())

    # Every section open, which is the worst case the width has to cover.
    for i in range(w._panel_layout.count()):
        it = w._panel_layout.itemAt(i).widget()
        if it is not None and hasattr(it, "set_expanded"):
            it.set_expanded(True)
    app.processEvents()
    print("all open sizeHint  :", panel.sizeHint().width())
    print("all open minHint   :", panel.minimumSizeHint().width())

    # With every section open, nothing may stick out past the panel's right edge.
    over = []
    for c in panel.findChildren(QWidget):
        if not isinstance(c, WATCHED):
            continue
        right = c.mapTo(panel, c.rect().topRight()).x()
        if right > panel.width():
            over.append((right - panel.width(), type(c).__name__,
                         c.text() if hasattr(c, "text") else ""))
    if over:
        print("\nSTICKS OUT past the right edge:")
        for d, cls, txt in sorted(over, reverse=True):
            print(f"  +{d:3d} px  {cls:<12} {txt[:40]}")
            bad.append(f"sticks out: {cls} {txt[:30]}")
    else:
        print("\nnothing sticks out past the right edge")

    # A button never sticks out — it shrinks and puts "…" in its own label instead,
    # which the check above cannot see. Qt's own size hint says how wide the control
    # has to be for its whole text, icon and padding to show; anything narrower than
    # that is reading as a cut-off word on screen.
    squashed = []
    for c in panel.findChildren(QWidget):
        if not isinstance(c, WATCHED) or not c.isVisible():
            continue
        # A control given a width on purpose (the 96 px number boxes, the 30 px
        # reset arrows) is meant to be that wide, and so is the inner field of a
        # spin box. Only controls left to the layout are of interest here.
        if c.minimumWidth() and c.minimumWidth() == c.maximumWidth():
            continue
        if isinstance(c, QLineEdit) and isinstance(c.parent(),
                                                   (QSpinBox, QDoubleSpinBox)):
            continue
        want = c.sizeHint().width()
        if want > c.width() + 1:
            squashed.append((want - c.width(), type(c).__name__,
                             c.text() if hasattr(c, "text") else "", c.width(), want))
    if squashed:
        print("\nTEXT CUT SHORT (control narrower than its own size hint):")
        for d, cls, txt, have, want in sorted(squashed, reverse=True):
            print(f"  -{d:3d} px  {cls:<12} {have:3d} of {want:3d}  {txt[:38]}")
            bad.append(f"text cut short: {cls} {txt[:30]}")
    else:
        print("\nno control is narrower than its own size hint")

    print("\nwidest controls inside the panel (minimumSizeHint width):")
    rows = []
    for c in panel.findChildren(QWidget):
        if not isinstance(c, WATCHED):
            continue
        mw = c.minimumSizeHint().width()
        rows.append((mw, type(c).__name__, (c.text() if hasattr(c, "text") else "")))
    for mw, cls, txt in sorted(rows, reverse=True)[:14]:
        print(f"  {mw:4d}  {cls:<12} {txt[:44]}")

    # Every window the Workshop can put up, so their controls are checked too.
    dialogs = [("tab", w),
               ("Line profile", wk_t.ProfileDialog()),
               ("Table", wk_t._TableDialog("Results")),
               ("Curve", wk_t._CurveDialog("Radial profile")),
               ("Play", wk_t._PlayDialog())]
    w._show_shortcuts()
    if getattr(w, "_keys_dlg", None) is not None:
        dialogs.append(("Shortcuts", w._keys_dlg))
    app.processEvents()

    print("\ncontrols with NO tooltip:")
    n = 0
    for where, top in dialogs:
        for c in top.findChildren(QWidget):
            own = type(c).__name__
            # A tick box's own line edit / a spin box's inner field has no tooltip of
            # its own and does not need one: Qt falls back to the parent's.
            if not (isinstance(c, WATCHED) or own.startswith("_")
                    or own == "WorkshopCanvas"):
                continue
            if isinstance(c, QLineEdit) and isinstance(c.parent(),
                                                       (QSpinBox, QDoubleSpinBox)):
                continue
            if own in EXEMPT or c.toolTip().strip():
                continue
            txt = c.text() if hasattr(c, "text") else ""
            print(f"  [{where}] {own:<16} {txt[:46]!r}")
            n += 1
            bad.append(f"no tooltip: {where} {own} {txt[:30]}")
    print(f"  -- {n} without a tooltip"
          + (f" (plus {', '.join(sorted(EXEMPT))} on purpose)" if n == 0 else ""))

    out = Path(__file__).with_name("workshop_panel.png")
    w.grab().save(str(out))
    print("\nwritten:", out)

    # The Fill box on its own, off and on, at six times the size — at 15 px in a
    # whole-window shot there is no telling an empty box from no box at all.
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPixmap
    cb = w._fill_cb
    scale = 6
    shots = []
    for on in (False, True):
        cb.setChecked(on)
        app.processEvents()
        shots.append(cb.grab())
    cb.setChecked(False)
    strip = QPixmap((shots[0].width() + 8) * len(shots) * scale,
                    (shots[0].height() + 8) * scale)
    strip.fill(Qt.GlobalColor.white)
    q = QPainter(strip)
    for i, pm in enumerate(shots):
        big = pm.scaled(pm.width() * scale, pm.height() * scale,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.FastTransformation)
        q.drawPixmap(int((pm.width() + 8) * scale * i + 4 * scale), 4 * scale, big)
    q.end()
    out2 = Path(__file__).with_name("workshop_fill_box.png")
    strip.save(str(out2))
    print("written:", out2)

    if bad:
        print("\nFAILED:")
        for b in bad:
            print("  " + b)
        return 1
    print("\nAll claims hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
