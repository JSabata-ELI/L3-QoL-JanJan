"""Workshop: the typed number boxes beside Contrast / Brightness / Gamma.

Checks that a typed value reaches the slider, that the arrows step 1 % of the
range, that a moved slider shows in the box, and renders the rows (normal and
with Auto ticked) to a PNG for a look by eye.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/render_wk_value_boxes.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_wk_valbox_"))
os.environ["APPDATA"] = str(_TMP)

import wk_t                                                        # noqa: E402
from render_workshop_panel import app_stylesheet                   # noqa: E402
from PySide6.QtCore import Qt                                      # noqa: E402
from PySide6.QtGui import QPainter, QPixmap                        # noqa: E402
from PySide6.QtWidgets import QApplication                         # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)

    w = wk_t.WorkshopWidget()
    w.resize(1600, 1000)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
    w.move(-3000, 0)
    w.show()
    for i in range(w._panel_layout.count()):
        it = w._panel_layout.itemAt(i).widget()
        if it is not None and hasattr(it, "set_expanded"):
            it.set_expanded(True)
    import numpy as np
    yy, xx = np.mgrid[0:200, 0:300]
    w.receive_image((40 + 150 * np.exp(-((xx - 150) ** 2 + (yy - 100) ** 2) / 2000)
                     ).astype(np.uint8), "test")
    app.processEvents()

    bad = []

    def check(what, got, want):
        ok = abs(got - want) < 1e-9
        print(f"{'ok ' if ok else 'BAD'} {what}: {got} (want {want})")
        if not ok:
            bad.append(what)

    c, b, g = w._contrast_val, w._bright_val, w._gamma_val
    check("contrast step", c.singleStep(), 8)
    check("brightness step", b.singleStep(), 5)
    check("gamma step", g.singleStep(), 0.01)
    c.setValue(-120)
    check("typed contrast -> slider", w._contrast_sl.value(), -120)
    g.setValue(0.75)
    check("typed gamma -> slider", w._gamma_sl.value(), 75)
    b.stepBy(2)
    check("two brightness arrow clicks -> slider", w._bright_sl.value(), 10)
    w._contrast_sl.setValue(33)
    check("slider -> contrast box", c.value(), 33)
    check("box range follows slider", g.maximum(), wk_t.GAMMA_SLIDER_MAX / 100)

    def rows_pixmap():
        sec = c.parentWidget()
        while sec is not None and not hasattr(sec, "set_expanded"):
            sec = sec.parentWidget()
        return (sec or c.parentWidget()).grab()

    shot_free = rows_pixmap()
    w._cb_auto_contrast.setChecked(True)
    w._cb_auto_bright.setChecked(True)
    w._apply_view_now()
    app.processEvents()
    print("auto ticked -> boxes enabled:", c.isEnabled(), b.isEnabled())
    if c.isEnabled() or b.isEnabled():
        bad.append("box still editable under Auto")
    shot_auto = rows_pixmap()

    out = QPixmap(shot_free.width() + shot_auto.width() + 20,
                  max(shot_free.height(), shot_auto.height()))
    out.fill(Qt.GlobalColor.white)
    p = QPainter(out)
    p.drawPixmap(0, 0, shot_free)
    p.drawPixmap(shot_free.width() + 20, 0, shot_auto)
    p.end()
    path = _TMP / "wk_value_boxes.png"
    out.save(str(path))
    print("picture:", path)
    w.close()
    print("RESULT:", "FAIL " + ", ".join(bad) if bad else "PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
