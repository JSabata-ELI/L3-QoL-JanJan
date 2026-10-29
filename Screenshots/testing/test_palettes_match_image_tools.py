"""The preview's palettes are Image Tools' palettes — same list, same colours.

Pinned:
  * the palette list is Image Tools' list, in its order ("Original" for "Default");
  * every table is identical to Image Tools' (is_t.GRADIENTS);
  * Binary and False Colors read RAW CAMERA COUNTS off an archive frame: the same
    intensity gets the same colour whatever bracket the archiver stored the frame at
    (it writes raw * 65535 / (2**bits - 1), bits from the frame's `MaxValue`);
  * those two ignore Contrast / Brightness / Gamma / Auto, as in Image Tools;
  * Binary is shrunk by picking whole pixels, never by averaging.

Image Tools' tables are read straight out of its is_t.py source — only the palette
block is executed, so no Qt is loaded and no window can appear.

Run:  python testing/test_palettes_match_image_tools.py
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
from PIL import Image, PngImagePlugin  # noqa: E402
import ssq_core as s  # noqa: E402

PW = s.Tone   # the preview's rendering, without the window
FAILED = []
IS_T = Path(__file__).resolve().parents[2] / "Image Tools" / "is_t.py"


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def image_tools_gradients():
    src = IS_T.read_text(encoding="utf-8")
    start = src.index("def _make_lut(")
    end = src.index("GRADIENT_NAMES = list(GRADIENTS.keys())")
    ns = {"np": np}
    exec(src[start:end], ns)
    return ns["GRADIENTS"]


def lut_of(name):
    if name not in PW._LUTS:
        PW._build_lut(name)
    return PW._LUTS[name]


def archive_frame(raw, bits):
    """A 16-bit PNG as the archiver writes it, read back through PIL."""
    stored = np.rint(np.asarray(raw, dtype=np.float64) * (65535.0 / (2 ** bits - 1)))
    img = Image.fromarray(stored.astype(np.uint16).reshape(1, -1))
    info = PngImagePlugin.PngInfo()
    info.add_text("MaxValue", f"{float(np.max(raw)):.6f}")
    buf = io.BytesIO()
    img.save(buf, "PNG", pnginfo=info)
    buf.seek(0)
    return Image.open(buf)


def test_same_list_and_tables():
    print("the same palettes as Image Tools")
    g = image_tools_gradients()
    want = ["Original" if n == "Default" else n for n in g]
    check("same list, same order", PW._PALETTES == want, f"{PW._PALETTES} vs {want}")
    for name, table in g.items():
        if table is None:
            continue
        check(f"{name}: identical table", np.array_equal(lut_of(name), table))


def test_ni_counts():
    print("Binary and False Colors read raw counts")
    for pal in ("Binary", "False Colors"):
        lut = lut_of(pal)
        same = True
        for bits in range(7, 13):
            raw = np.arange(2 ** bits)
            raw[-1] = 2 ** bits - 1          # the frame's peak sets the bracket
            got, _ = PW._render_tuned(archive_frame(raw, bits), pal, False, 0, False, 0, 1.0)
            want = lut[np.minimum(raw, 4095) >> 4].reshape(1, -1, 3)
            same = same and np.array_equal(np.array(got), want)
        check(f"{pal}: one colour per 16 counts on brackets 7..12", same)
    # Two bands read off the NI screenshot of 2026-10-05 (C03-084-PCW3DF, 8-bit frame).
    got = np.array(PW._render_tuned(archive_frame([80, 95, 96, 111, 255], 8),
                                    "Binary", False, 0, False, 0, 1.0)[0])[0]
    check("Binary: 80..95 magenta, 96..111 cyan as in the NI window",
          [tuple(c) for c in got[:4]] == [(255, 0, 255)] * 2 + [(0, 255, 255)] * 2,
          str(got[:4].tolist()))


def test_view_only():
    print("the tone controls do nothing on an NI palette")
    img = archive_frame(np.arange(0, 4096, 7), 12)
    ref = np.array(PW._render_tuned(img, "Binary", False, 0, False, 0, 1.0)[0])
    for args in ((True, 0, False, 0, 1.0), (False, 80, False, 0, 1.0),
                 (False, 0, True, 0, 1.0), (False, 0, False, 60, 1.0),
                 (False, 0, False, 0, 2.5)):
        got = np.array(PW._render_tuned(img, "Binary", *args)[0])
        check(f"Binary unchanged with {args}", np.array_equal(got, ref))


def test_pixel_exact_shrink():
    print("Binary is shrunk by picking pixels")
    check("Binary resamples NEAREST", PW._resample_for("Binary") == Image.NEAREST)
    check("other palettes keep the smooth filter",
          PW._resample_for("False Colors") == Image.LANCZOS
          and PW._resample_for("Hot") == Image.LANCZOS)
    img = archive_frame(np.tile([100, 300], 50), 12)
    small = PW._fit_to_box(img.resize((100, 4)), 50, PW._resample_for("Binary"))
    vals = set(np.unique(np.array(small)).tolist())
    stored = set(np.unique(np.array(img)).tolist())
    check("no new in-between values appear", vals <= stored, f"{vals} vs {stored}")
    check("the frame's MaxValue survives the resize",
          small.info.get("MaxValue") == img.info.get("MaxValue"))


if __name__ == "__main__":
    test_same_list_and_tables()
    test_ni_counts()
    test_view_only()
    test_pixel_exact_shrink()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        sys.exit(1)
    print("all checks passed")
