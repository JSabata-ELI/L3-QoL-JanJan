"""The NI Vision "Binary" palette: one colour per 16 raw counts, on every bracket.

Measured 2026-10-05 by pairing two lab screenshots of the NI viewer (16 camera windows,
frames bracketed at 7..12 bits) with the archive frames they showed — see the comment
above `_NI_BINARY_CYCLE` in is_t.py. The rule:

    entry  = raw_counts >> 4            (12-bit sensor -> 0..255)
    colour = black (0), white (255), else _NI_BINARY_CYCLE[(entry-1) % 15]

What is pinned here:
  * the 16-bit render path gives that colour for EVERY raw count of EVERY bracket the
    archiver writes (7..12 bits) — the old rule ("a new colour every 1024 stored units")
    was right only on 10-bit frames, which is where it had been measured;
  * a handful of bands read straight off the screenshots (C03-084-PCW3DF, 8-bit bracket:
    raw 80..95 magenta, 96..111 cyan, 112..127 orange, 128..143 rose, 144..159
    chartreuse; saturated pixels white);
  * the 8-bit tables in Finder (if_t) and Shot Finder (sf_t) are the same 256 entries.

Run from the program folder:  python testing/test_ni_binary_palette.py
"""
import importlib.util
import sys

import bench_common as B   # sets the offscreen Qt platform before Qt loads

import numpy as np

S = B.load_slider()
img_scale = S.img_scale

CYCLE = np.array(S._NI_BINARY_CYCLE, dtype=np.uint8)
fails = []


def expected(raw):
    e = np.asarray(raw) >> 4
    out = CYCLE[(np.maximum(e, 1) - 1) % 15].copy()
    out[e == 0] = (0, 0, 0)
    out[e >= 255] = (255, 255, 255)
    return out


# 1. Every raw count on every bracket, through the shipping 16-bit path.
for bits in range(7, img_scale.SENSOR_BITS + 1):
    top = 2 ** bits - 1
    raw = np.arange(top + 1, dtype=np.int64)
    stored = np.rint(raw * (65535.0 / top)).astype(np.uint16).reshape(1, -1)
    fs = img_scale.display_full_scale(bits)
    got = S._ni_binary_rgb(stored, fs)[0]
    bad = np.where(np.any(got != expected(raw), axis=1))[0]
    if len(bad):
        fails.append(f"{bits}-bit bracket: {len(bad)} of {top + 1} counts wrong, "
                     f"first raw={bad[0]} got {tuple(got[bad[0]])}")

# 2. Bands read off the 2026-10-05 screenshot (C03-084-PCW3DF, 8-bit bracket).
SEEN = [((80, 95), (255, 0, 255)), ((96, 111), (0, 255, 255)), ((112, 127), (255, 127, 0)),
        ((128, 143), (255, 0, 127)), ((144, 159), (127, 255, 0))]
fs8 = img_scale.display_full_scale(8)
for (lo, hi), rgb in SEEN:
    # Raw sensor counts; the archiver stores an 8-bit-bracket frame as counts * 257.
    raw = np.arange(lo, hi + 1)
    st = np.rint(raw * 257.0).astype(np.uint16).reshape(1, -1)
    got = S._ni_binary_rgb(st, fs8)[0]
    if not np.all(got == np.array(rgb, dtype=np.uint8)):
        fails.append(f"8-bit frame values {lo}..{hi}: expected {rgb}, got "
                     f"{sorted(set(map(tuple, got.tolist())))}")
sat = S._ni_binary_rgb(np.array([[65535]], dtype=np.uint16), 65535.0)[0, 0]
if tuple(sat) != (255, 255, 255):
    fails.append(f"saturated pixel is {tuple(sat)}, NI shows white")

# 3. The 8-bit tables of the other tabs are the same table.
ref = S.GRADIENTS["Binary"]
if not np.array_equal(ref, S._NI_BINARY_LUT):
    fails.append("is_t GRADIENTS['Binary'] differs from _NI_BINARY_LUT")
for fname, attr in (("if_t.py", "_make_ni_binary_lut"), ("sf_t.py", "_make_ni_binary_lut_sf")):
    src = (B.HERE / fname).read_text(encoding="utf-8")
    # Only the palette block is executed: importing the whole tab would build its GUI.
    start = src.index("_NI_BINARY_CYCLE")
    start = src.rfind("\n", 0, start) + 1
    end = src.index("\ndef ", src.index("def " + attr) + 4)
    ns = {"np": np, "_np_grad": np}
    exec(src[start:end], ns)
    if not np.array_equal(ns[attr](), ref):
        fails.append(f"{fname} {attr}() differs from is_t's Binary table")

if fails:
    print("FAIL")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("OK  NI Binary: 16 counts per colour on brackets 7..12, screenshot bands, "
      "tab tables identical")
