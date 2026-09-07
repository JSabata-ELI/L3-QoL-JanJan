"""Render the Spectra sidebar so the new "Unit" box can be looked at.

The panel is narrow, and a control added to it can squash its neighbours or hide
a button. Screenshot with the real Windows backend — offscreen has no fonts and
lies about text size.

Writes testing/_out/unit_box.png (card 3, at 3x so the text can be judged) and
prints the geometry plus whether the Change… button is actually enabled.
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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sp_t import SpectraWidget

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
os.makedirs(OUT, exist_ok=True)

app = QApplication.instance() or QApplication(sys.argv)
# The tab lives inside CSS Logger, which sets Fusion plus a light app stylesheet.
# Without it every unstyled label and button renders in the dark theme's white
# and the shot lies about what the operator sees.
app.setStyle("Fusion")
try:
    import main as css_logger
    app.setStyleSheet(css_logger._APP_STYLESHEET)
except Exception as exc:                    # pragma: no cover - shot helper only
    print("could not load the host stylesheet:", exc)

w = SpectraWidget()
w._spec_base_pv = "L3-SBDP-SPIDER:TimeDomain_Int"
w._spec_x_pv = w._spec_base_pv + "_X"
w._spec_y_pv = w._spec_base_pv + "_Y"
w._x_axis_cfg = {"mode": "native"}
w._lbl_spec_base.setText(w._spec_base_pv)
w._lbl_spec_pair.setText(f"X: {w._x_axis_summary()}   /   Y: {w._spec_y_pv}")
w._edit_x_unit.setText(w._x_unit())
w._sync_x_unit_labels()
w.resize(1500, 950)
w.show()
app.processEvents()

card = w._edit_x_unit.parentWidget()
print("card width:", card.width())
print("Unit box:", w._edit_x_unit.geometry(), "text:", repr(w._edit_x_unit.text()))
print("Change button:", w._btn_spec.geometry(), "enabled:", w._btn_spec.isEnabled())
print("range group title:", w._g_xrange.title())
print("From/To spinboxes:", w._sb_x_min.geometry(), w._sb_x_max.geometry())

pm = card.grab()
pm = pm.scaled(pm.width() * 3, pm.height() * 3,
               Qt.AspectRatioMode.KeepAspectRatio,
               Qt.TransformationMode.SmoothTransformation)
path = os.path.join(OUT, "unit_box.png")
pm.save(path)
print("wrote", path)

pm2 = w._g_xrange.grab()
pm2 = pm2.scaled(pm2.width() * 3, pm2.height() * 3,
                 Qt.AspectRatioMode.KeepAspectRatio,
                 Qt.TransformationMode.SmoothTransformation)
p2 = os.path.join(OUT, "unit_range_group.png")
pm2.save(p2)
print("wrote", p2)
