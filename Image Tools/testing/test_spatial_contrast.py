"""Spatial Contrast: the Image Slider and the Workshop give the same numbers.

A synthetic 16-bit beam is written to a PNG. The Slider's task measures the file;
the Workshop measures the same frame handed over in memory (raw counts, full scale
65535). SC = Max / Mean over the thresholded, hole-filled beam must agree, the
top-intensity circles must reach the canvas, and an edit of the picture must take
the old result off the panel. Also renders the panel group to
workshop_analysis_panel.png.

Run:  python testing/test_spatial_contrast.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np                                    # noqa: E402
from PIL import Image                                 # noqa: E402
from PySide6.QtCore import Qt                         # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

import is_t                                           # noqa: E402
sys.modules.setdefault("image_slider", is_t)          # what main.py registers
import wk_t                                           # noqa: E402

FAILED: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(f"{name} {detail}")


def beam(h=240, w=320):
    y, x = np.mgrid[0:h, 0:w]
    g = 40000.0 * np.exp(-(((x - 170) / 45.0) ** 2 + ((y - 110) / 35.0) ** 2))
    g += 300.0 + 50.0 * np.sin(x / 7.0)          # background with some structure
    g[110, 170] = 52000.0                         # one hot pixel at the top
    return np.clip(g, 0, 65535).astype(np.uint16)


def wait(app, cond, timeout=10.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < timeout:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return cond()


def slider_result(path, thr):
    """The Slider's own task, run in this thread."""
    sig = is_t._SCSignals()
    got = {}
    sig.finished.connect(lambda r: got.setdefault("r", r))
    is_t._SCTask(path, thr, sig).run()
    return got.get("r")


def main():
    app = QApplication([])
    # The look main.py gives the whole program.
    app.setStyle("Fusion")
    app.setStyleSheet("QWidget { background: #f3f3f3; color: #111; }"
                      "QLabel { background: transparent; }"
                      "QPushButton { padding: 5px 8px; }")
    arr16 = beam()
    tmp = Path(tempfile.mkdtemp()) / "beam16.png"
    Image.fromarray(arr16).save(str(tmp))
    thr = 5000

    print("\nThe shared maths")
    r_fn, a8, mask = is_t.spatial_contrast(arr16, thr)
    check("SC = Max / Mean", abs(r_fn["sc"] - r_fn["max"] / r_fn["mean"]) < 1e-9,
          f"{r_fn['sc']:.4f}")
    check("the hot pixel is the first of the top list",
          (r_fn["top_xs"][0], r_fn["top_ys"][0]) == (170, 110))
    r_none, _a, _m = is_t.spatial_contrast(arr16, 60000)
    check("a threshold above everything says so", r_none["error"] is not None
          and r_none["sc"] is None)
    excl = np.zeros(arr16.shape, bool)
    excl[100:121, 160:181] = True
    r_ex, _a, _m = is_t.spatial_contrast(arr16, thr, excl)
    check("an excluded hot spot lowers the max", r_ex["max"] < r_fn["max"],
          f"{r_ex['max']:.0f} < {r_fn['max']:.0f}")

    print("\nImage Slider (file) and Workshop (memory) agree")
    r_sl = slider_result(tmp, thr)
    check("the Slider measured the file", r_sl is not None and not r_sl["error"])
    check("same as the shared function", abs(r_sl["sc"] - r_fn["sc"]) < 1e-9)

    w = wk_t.WorkshopWidget()
    w.resize(1300, 850)
    rgb8 = np.repeat((arr16 / 257).astype(np.uint8)[..., None], 3, axis=2)
    w.receive_image(rgb8, "beam16", raw=arr16.astype(np.float32),
                    full_scale=65535.0)
    app.processEvents()
    w._sc_threshold_sb.setValue(thr)
    w._sc_topn_sb.setValue(5)
    w._run_spatial_contrast()
    check("the Workshop finished", wait(app, lambda: not w._sc_running))
    sc_txt = w._sc_val_sc.text()
    check("same SC on the panel", sc_txt == f"{r_sl['sc']:.4f}",
          f"{sc_txt} vs {r_sl['sc']:.4f}")
    check("same pixel count", w._sc_val_beam.text().startswith(f"{r_sl['n_beam']:,}"),
          w._sc_val_beam.text())
    marks = w._canvas.sc_marks
    check("five circles handed to the canvas", marks is not None and len(marks[1]) == 5)
    check("the preview is shown", w._sc_preview_pixmap is not None)

    print("\nAuto threshold")
    w._run_sc_auto_threshold()
    wait(app, lambda: not w._sc_running)
    otsu = is_t._SCTask.otsu_threshold_raw(arr16.astype(np.float32), 65535.0)
    check("Otsu value set", w._sc_threshold_sb.value() == otsu,
          f"{w._sc_threshold_sb.value()} vs {otsu}")

    print("\nAn edit takes the old answer away")
    w._flip(1)
    app.processEvents()
    check("SC cleared", w._sc_val_sc.text() == "—", w._sc_val_sc.text())
    check("circles gone", w._canvas.sc_marks is None)
    check("and the panel says why", "changed" in w._sc_status_lbl.text(),
          w._sc_status_lbl.text())

    # Render the group itself, shown without taking the focus.
    w._run_spatial_contrast()
    wait(app, lambda: not w._sc_running)
    sec = w._sc_val_sc.parentWidget()
    while sec is not None and not hasattr(sec, "set_expanded"):
        sec = sec.parentWidget()
    if sec is not None:
        sec.set_expanded(True)
    w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    w.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
    w.move(-3000, 0)
    w.show()
    for _ in range(10):
        app.processEvents()
    if sec is not None:
        out = HERE / "workshop_analysis_panel.png"
        sec.grab().save(str(out))
        print(f"\nscreenshot: {out}")
        out2 = HERE / "workshop_analysis_canvas.png"
        w._canvas.grab().save(str(out2))
        print(f"screenshot: {out2}")
    w.hide()

    if FAILED:
        print(f"\n{len(FAILED)} FAILED:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
