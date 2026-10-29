"""The one-lookup NI render (_ni_rgb32_image) paints exactly what the old route did.

The old route for a Binary / False Colors frame was
    _rgb_to_qimage(_ni_binary_rgb(arr16, full_scale))
— about six full-frame passes. _ni_rgb32_image replaces it with one gather through a
precomputed 65536-entry table. Pinned here: every stored 16-bit value, on every bracket
the archiver writes, for both palettes, gives the same RGB32 pixel; plus a timing line
so the speed-up is visible.

Run from the program folder:  python testing/test_ni_rgb32_table.py
"""
import time

import bench_common as B   # sets the offscreen Qt platform before Qt loads

import numpy as np

S = B.load_slider()
img_scale = S.img_scale

fails = []


def pixels(img):
    ptr = img.constBits()
    w, h = img.width(), img.height()
    a = np.frombuffer(ptr, dtype=np.uint32, count=h * img.bytesPerLine() // 4)
    return a.reshape(h, img.bytesPerLine() // 4)[:, :w].copy()


values = np.arange(65536, dtype=np.uint16).reshape(256, 256)
scales = [65535.0] + [img_scale.display_full_scale(b)
                      for b in range(7, img_scale.SENSOR_BITS + 1)]
for binary in (True, False):
    old_fn = S._ni_binary_rgb if binary else S._ni_false_colors_rgb
    for fs in scales:
        old = pixels(S._rgb_to_qimage(old_fn(values, fs)))
        new = pixels(S._ni_rgb32_image(values, fs, binary=binary))
        n_bad = int(np.count_nonzero(old != new))
        if n_bad:
            fails.append(f"{'Binary' if binary else 'False Colors'} fs={fs}: "
                         f"{n_bad} of 65536 values differ")

# Timing on a camera-sized frame.
rng = np.random.default_rng(1)
frame = rng.integers(0, 4096, size=(2160, 2560), dtype=np.uint16) * 16
fs = img_scale.display_full_scale(12)
S._ni_rgb32_image(frame, fs, binary=True)   # build the table once
t0 = time.perf_counter()
for _ in range(5):
    S._rgb_to_qimage(S._ni_binary_rgb(frame, fs))
t_old = (time.perf_counter() - t0) / 5
t0 = time.perf_counter()
for _ in range(5):
    S._ni_rgb32_image(frame, fs, binary=True)
t_new = (time.perf_counter() - t0) / 5
print(f"2560x2160 Binary frame: old {t_old * 1000:.0f} ms, new {t_new * 1000:.0f} ms")

if fails:
    print("FAIL")
    for f in fails:
        print("  " + f)
    raise SystemExit(1)
print("OK — one-lookup NI render is pixel-identical on every bracket")
