"""LOOK at the Image Slider's IMAGE / DISPLAY section, under main.py's own stylesheet.

    QT_QPA_PLATFORM=windows PYTHONIOENCODING=utf-8 python testing/render_display_rows.py

Offscreen has no fonts and lies about text width, so this uses the real windows
platform. Two shots, written beside this file:

  display_rows.png        the three rows as they normally stand
  display_rows_mixed.png  the same rows with two cameras selected that disagree —
                          the "not the same value" mark and the warning line

It also prints what each row is showing and whether anything sticks out past the
panel's right edge, which is the failure the wider Contrast range could cause.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_rows_"))
os.environ["APPDATA"] = str(_TMP)

import img_scale                                                    # noqa: E402
import is_t                                                         # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget                 # noqa: E402


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


def shoot(w: QWidget, name: str) -> None:
    out = Path(__file__).resolve().parent / name
    w.grab().save(str(out))
    print(f"  wrote {out.name}  ({w.width()}x{w.height()})")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    css = app_stylesheet()
    if css:
        app.setStyleSheet(css)
    else:
        print("WARNING: no app stylesheet read")

    v = is_t.Viewer()
    v.resize(1500, 950)
    v.show()
    app.processEvents()

    # Every collapsible section open — a collapsed one hides its own widgets, and a
    # hidden widget cannot be looked at.
    for c in v.findChildren(QWidget):
        if hasattr(c, "set_expanded"):
            c.set_expanded(True)
    app.processEvents()

    # The body of the section the three rows live in.
    box = v.contrast_slider.parentWidget()
    app.processEvents()

    print("rows as they stand")
    print(f"  Con {v.lbl_contrast_val.text():>6}   range "
          f"{v.contrast_slider.minimum()}..{v.contrast_slider.maximum()}")
    print(f"  Bri {v.lbl_bright_val.text():>6}   range "
          f"{v.brightness_slider.minimum()}..{v.brightness_slider.maximum()}")
    print(f"  Gam {v.lbl_gamma_val.text():>6}   range "
          f"{v.gamma_slider.minimum()}..{v.gamma_slider.maximum()}")
    print(f"  gain at the top of the Contrast slider: "
          f"{img_scale.contrast_gain(v.contrast_slider.maximum()):.0f}x")
    shoot(box, "display_rows.png")

    # Same rows with the widest numbers they can ever show, so the readout column is
    # measured at its worst rather than at zero.
    v.contrast_slider.setValue(img_scale.CONTRAST_MIN)
    v.brightness_slider.setValue(img_scale.BRIGHTNESS_MIN)
    v.gamma_slider.setValue(img_scale.GAMMA_SLIDER_MIN)
    app.processEvents()
    print("rows at their widest numbers")
    print(f"  Con {v.lbl_contrast_val.text():>6}  Bri {v.lbl_bright_val.text():>6}  "
          f"Gam {v.lbl_gamma_val.text():>6}")
    shoot(box, "display_rows_wide.png")

    # Two cameras that disagree: the mark in the readout and the warning line.
    v.contrast_slider.setValue(0)
    v.brightness_slider.setValue(0)
    v.gamma_slider.setValue(img_scale.GAMMA_SLIDER_NEUTRAL)
    v._cam_names = ["C01-A-CAM1NF", "C02-B-CAM2NF"]
    v._cam_items = [[], []]
    v._multi_grid.setup_cameras(v._cam_names)
    v._cam_disp_reset(2)
    v._cam_disp[1]["ui"] = dict(v._cam_disp[1]["ui"], bri=60, auto_c=True)
    v._multi_grid._on_cam_clicked(0)
    v._multi_grid._on_cam_clicked(1)
    v._load_disp_from_targets()
    app.processEvents()
    print("two cameras selected that do not agree")
    print(f"  Con {v.lbl_contrast_val.text():>6}  Bri {v.lbl_bright_val.text():>6}  "
          f"Gam {v.lbl_gamma_val.text():>6}")
    print(f"  warning visible: {v.lbl_disp_mixed.isVisible()}")
    print(f"  warning text   : {v.lbl_disp_mixed.text()!r}")
    shoot(box, "display_rows_mixed.png")

    # Nothing in these rows may stick out past the panel.
    panel = box
    over = []
    for c in box.findChildren(QWidget):
        # Only what is laid out inside the panel: a combo's drop-down list is a popup
        # window of its own and is always "wider than the panel".
        if not c.isVisible() or c.window() is not panel.window():
            continue
        right = c.mapTo(panel, c.rect().topRight()).x()
        if right > panel.width() + 1:
            over.append((right - panel.width(), type(c).__name__))
    print("sticks out past the right edge:",
          sorted(over, reverse=True) if over else "nothing")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
