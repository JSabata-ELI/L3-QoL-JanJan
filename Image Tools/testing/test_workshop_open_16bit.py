"""Workshop: a 16-bit grey file opened with "Open file" shows its picture, not white.

Pillow's convert("RGB") clips every value above 255 instead of scaling it, so a camera
frame opened from disk came out almost entirely white and the display window had
nothing left to work with. This writes 16-bit PNGs (a 12-bit frame in a 16-bit
container, a full-range one, an archive frame with a MaxValue bracket) and an 8-bit
one, opens each through open_files, and checks the picture, the counts kept for
measuring, and that moving the display window changes what is drawn.

No window is shown.

Run:  python testing/test_workshop_open_16bit.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np                                    # noqa: E402
from PIL import Image, PngImagePlugin                 # noqa: E402
from PySide6.QtWidgets import QApplication            # noqa: E402

import is_t                                           # noqa: E402
sys.modules.setdefault("image_slider", is_t)          # what main.py registers
import wk_t                                           # noqa: E402

FAILED: list = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILED.append(f"{name} {detail}")


def beam(peak, h=200, w=260):
    y, x = np.mgrid[0:h, 0:w]
    g = peak * np.exp(-(((x - 130) / 40.0) ** 2 + ((y - 100) / 30.0) ** 2))
    g += peak * 0.02
    return np.rint(g).astype(np.uint16)


def open_one(w, path):
    before = len(w._slots)
    w.open_files([str(path)])
    return w._slots[-1] if len(w._slots) > before else None


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    w = wk_t.WorkshopWidget()
    tmp = Path(tempfile.mkdtemp(prefix="wk16_"))

    # 1. A 12-bit camera frame stored as plain 16-bit numbers, no bracket.
    a12 = beam(4000)
    p12 = tmp / "frame12.png"
    Image.fromarray(a12).save(p12)
    print("\n12-bit frame in a 16-bit file, no bracket")
    s = open_one(w, p12)
    check("opened", s is not None)
    check("picture stays grey (one channel)", s.base.ndim == 2, str(s.base.shape))
    white = float((s.base == 255).mean())
    check("picture is not white", white < 0.01, f"white share {white:.4f}")
    check("peak reaches the top of the picture", int(s.base.max()) >= 250,
          str(int(s.base.max())))
    check("background is dark", int(np.percentile(s.base, 5)) < 20,
          str(int(np.percentile(s.base, 5))))
    check("counts kept unchanged for measuring",
          s.measures_raw() and np.array_equal(s.raw, a12))
    check("full scale is the 12-bit range", s.full_scale == 4095.0, str(s.full_scale))
    check("no background re-read pending", "reading" not in s.raw_note, s.raw_note)
    # Every count maps to its nearest display code — no more lost than 8 bits must lose.
    expect = np.rint(a12.astype(np.float64) * 255.0 / 4095.0).astype(np.uint8)
    check("display codes are the rounded counts", np.array_equal(s.base, expect))

    # The display window now changes what is drawn.
    w._activate_slot(len(w._slots) - 1)
    img0 = wk_t.render_view(s.base, s.view).copy()
    s.view.win_lo, s.view.win_hi = 0, 60
    img1 = wk_t.render_view(s.base, s.view)
    s.view.win_lo, s.view.win_hi = 0, 255
    check("moving the white point changes the picture", not np.array_equal(img0, img1))

    # 2. Full-range 16-bit data.
    a16 = beam(60000)
    p16 = tmp / "frame16.png"
    Image.fromarray(a16).save(p16)
    print("\nfull-range 16-bit file")
    s = open_one(w, p16)
    check("full scale is the 16-bit range", s.full_scale == 65535.0, str(s.full_scale))
    check("picture is not white", float((s.base == 255).mean()) < 0.01)
    check("counts kept", np.array_equal(s.raw, a16))

    # 3. An archive frame with a MaxValue bracket: un-stretched exactly as before.
    counts = beam(1000)                                  # 10-bit bracket (1023)
    stored = np.rint(counts.astype(np.float64) * 65535.0 / 1023.0).astype(np.uint16)
    pb = tmp / "archive.png"
    meta = PngImagePlugin.PngInfo()
    meta.add_text("MaxValue", str(int(counts.max())))
    Image.fromarray(stored).save(pb, pnginfo=meta)
    print("\narchive frame with a bracket")
    s = open_one(w, pb)
    check("counts un-stretched", np.array_equal(s.raw, counts),
          f"max {int(s.raw.max())}")
    check("full scale is the sensor range", s.full_scale == 4095.0, str(s.full_scale))
    check("dim frame is dim, not white", int(s.base.max()) < 80, str(int(s.base.max())))

    # 4. An ordinary 8-bit file is untouched.
    a8 = (np.arange(200 * 260) % 256).reshape(200, 260).astype(np.uint8)
    p8 = tmp / "plain8.png"
    Image.fromarray(a8).save(p8)
    print("\n8-bit file")
    s = open_one(w, p8)
    check("8-bit picture unchanged", np.array_equal(s.base, a8))

    # 5. The background reader behind a sent picture still reads the same numbers.
    print("\nbackground reader")
    with Image.open(str(pb)) as im:
        arr, fs, _cam, _note, bracketed = wk_t._read_native(im, pb)
    check("reader un-stretches the archive frame",
          bracketed and np.array_equal(arr, counts) and fs == 4095.0)

    w.deleteLater()
    app.processEvents()
    print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED"))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
