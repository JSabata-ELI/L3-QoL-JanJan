"""The viewing controls: contrast, brightness, gamma, auto modes and palettes.

The one that matters for the reported complaint: with the "Original" palette a
colour picture keeps its colours even with Auto contrast on. Before, touching
any control dropped the picture into the grayscale pipeline, so Auto contrast
looked like it was also changing the palette.

Run:  python testing/test_tone.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402
import s  # noqa: E402

PW = s.PreviewWindow
FAILED = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


def gray_ramp():
    return np.tile(np.arange(256, dtype=np.uint8), (4, 1))


def test_neutral():
    print("nothing touched = nothing changed")
    arr = gray_ramp()
    lut, applied = PW._tone_lut(arr, False, 0, False, 0, 1.0)
    check("the curve is the identity", np.array_equal(lut, np.arange(256)))
    check("no contrast reported", applied["contrast"] == 0)
    check("no brightness reported", applied["brightness"] == 0)


def test_manual():
    print("the sliders")
    arr = gray_ramp()
    lut, _ = PW._tone_lut(arr, False, 0, False, 40, 1.0)
    check("brightness is a plain offset", lut[100] == 140 and lut[250] == 255)
    lut, _ = PW._tone_lut(arr, False, 0, False, -40, 1.0)
    check("a negative offset works too", lut[100] == 60 and lut[10] == 0)

    lut, _ = PW._tone_lut(arr, False, 60, False, 0, 1.0)
    check("contrast pivots on mid grey", abs(int(lut[128]) - 128) <= 1)
    check("contrast spreads the ends apart", lut[200] > 200 and lut[56] < 56)


def test_gamma():
    print("gamma")
    arr = gray_ramp()
    lut, _ = PW._tone_lut(arr, False, 0, False, 0, 1.0)
    check("gamma 1 changes nothing", np.array_equal(lut, np.arange(256)))

    up, _ = PW._tone_lut(arr, False, 0, False, 0, 2.0)
    check("above 1 lifts the midtones", up[128] > 150, f"{up[128]}")
    check("the black end stays put", up[0] == 0)
    check("the white end stays put", up[255] == 255)

    down, _ = PW._tone_lut(arr, False, 0, False, 0, 0.5)
    check("below 1 deepens the midtones", down[128] < 80, f"{down[128]}")
    check("the ends still stay put", down[0] == 0 and down[255] == 255)
    check("the curve never goes backwards", bool(np.all(np.diff(up.astype(int)) >= 0)))


def test_auto():
    print("the auto modes")
    # A flat, dull image: everything between 90 and 130.
    dull = np.random.default_rng(3).integers(90, 131, size=(64, 64)).astype(np.uint8)
    lut, applied = PW._tone_lut(dull, True, 0, False, 0, 1.0)
    out = lut[dull]
    check("auto contrast fills the range", out.min() < 20 and out.max() > 235,
          f"{out.min()}..{out.max()}")
    check("auto contrast reports a slider position", applied["contrast"] > 0,
          str(applied["contrast"]))

    dark = np.random.default_rng(4).integers(0, 60, size=(64, 64)).astype(np.uint8)
    lut, applied = PW._tone_lut(dark, False, 0, True, 0, 1.0)
    out = lut[dark]
    check("auto brightness lifts the top to white", out.max() >= 250, f"{out.max()}")
    check("auto brightness reports its offset", applied["brightness"] > 150,
          str(applied["brightness"]))
    check("auto brightness is a shift, not a stretch",
          abs(int(out.max()) - int(out.min()) - (int(dark.max()) - int(dark.min()))) <= 2)

    # Auto overrides the manual slider rather than adding to it.
    lut_a, _ = PW._tone_lut(dull, True, 0, False, 0, 1.0)
    lut_b, _ = PW._tone_lut(dull, True, 120, False, 0, 1.0)
    check("auto contrast ignores the manual slider", np.array_equal(lut_a, lut_b))


def test_colour_kept():
    print("a colour picture keeps its colours")
    # A dull red-ish gradient — clearly not grey.
    h, w = 32, 64
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[..., 0] = np.linspace(60, 120, w).astype(np.uint8)
    rgb[..., 1] = 40
    rgb[..., 2] = 20
    img = Image.fromarray(rgb, "RGB")

    out, _ = PW._render_tuned(img, "Original", False, 0, False, 0, 1.0)
    a = np.array(out)
    check("untouched: the picture comes back as it was", np.array_equal(a, rgb))

    for label, kwargs in [
        ("auto contrast", dict(auto_contrast=True, contrast=0, auto_bright=False,
                               brightness=0, gamma=1.0)),
        ("the contrast slider", dict(auto_contrast=False, contrast=50, auto_bright=False,
                                     brightness=0, gamma=1.0)),
        ("the brightness slider", dict(auto_contrast=False, contrast=0, auto_bright=False,
                                       brightness=30, gamma=1.0)),
        ("auto brightness", dict(auto_contrast=False, contrast=0, auto_bright=True,
                                 brightness=0, gamma=1.0)),
        ("gamma", dict(auto_contrast=False, contrast=0, auto_bright=False,
                       brightness=0, gamma=1.8)),
    ]:
        out, _ = PW._render_tuned(img, "Original", **kwargs)
        a = np.array(out)
        colourful = bool((a[..., 0].astype(int) - a[..., 2].astype(int)).max() > 10)
        check(f"still in colour with {label}", colourful)

    out, _ = PW._render_tuned(img, "Grayscale", True, 0, False, 0, 1.0)
    a = np.array(out)
    check("Grayscale really is grey",
          np.array_equal(a[..., 0], a[..., 1]) and np.array_equal(a[..., 1], a[..., 2]))

    out, _ = PW._render_tuned(img, "Hot", False, 0, False, 0, 1.0)
    a = np.array(out)
    check("a false-colour palette still maps the grey values",
          not np.array_equal(a[..., 0], a[..., 2]))


def test_grayscale_source():
    print("a grayscale picture")
    arr = np.tile(np.linspace(20, 200, 64).astype(np.uint8), (32, 1))
    img = Image.fromarray(arr, "L")
    out, _ = PW._render_tuned(img, "Original", False, 0, False, 0, 1.0)
    a = np.array(out)
    check("Original leaves a grey source grey",
          np.array_equal(a[..., 0], a[..., 1]) and np.array_equal(a[..., 0], arr))


def test_palette_list():
    print("the palette list")
    check("Original is offered", "Original" in PW._PALETTES)
    check("Original comes first", PW._PALETTES[0] == "Original")
    check("Grayscale is still offered", "Grayscale" in PW._PALETTES)


if __name__ == "__main__":
    test_neutral()
    test_manual()
    test_gamma()
    test_auto()
    test_colour_kept()
    test_grayscale_source()
    test_palette_list()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        sys.exit(1)
    print("all ok")
