"""What a region says about its own size, and whether its area is honest.

Two numbers beside a round shape were read as two radii. They never were: they are
the outside measurement of the shape, so for an ellipse they are the FULL axes. The
caption now says so — a circle gives one number with a ⌀ in front, an ellipse says
"axes", a rectangle is unchanged.

The second half checks the claim behind the area: the pixel count comes from the
real elliptical mask, so it has to land on π·a·b and nowhere near the width times
the height.
"""
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np                                      # noqa: E402

import wk_t                                             # noqa: E402


class _Slot:
    """Only what the caption reads out of a slot."""
    def __init__(self, px_per_mm=0.0):
        self.px_per_mm = px_per_mm


def _annot(kind, w, h):
    return wk_t._Annot(kind=kind, pts=[[10.0, 10.0], [10.0 + w, 10.0 + h]])


def _caption(kind, w, h, px_per_mm=0.0):
    a = _annot(kind, w, h)
    arr = np.full((400, 600), 100, dtype=np.uint16)
    st = wk_t.region_stats(arr, wk_t._region_mask(a, arr.shape))
    return wk_t._region_caption(a, st, _Slot(px_per_mm), "pixel intensity")


def test_circle_says_one_diameter():
    size = _caption(wk_t.A_ROI_ELLIPSE, 139, 139).split("\n")[1]
    assert size.startswith("⌀ 139 px"), size
    assert "×" not in size, f"a circle must not read as two numbers: {size}"


def test_ellipse_says_axes():
    size = _caption(wk_t.A_ROI_ELLIPSE, 254, 194).split("\n")[1]
    assert size.startswith("axes 254 × 194 px"), size


def test_rectangle_is_unchanged():
    size = _caption(wk_t.A_ROI_RECT, 254, 194).split("\n")[1]
    assert size.startswith("254 × 194 px"), size
    assert "axes" not in size


def test_real_size_follows_the_shape():
    #  4.5 µm pixels -> 1000 / 4.5 px per mm.
    ppm = 1000.0 / 4.5
    circle = _caption(wk_t.A_ROI_ELLIPSE, 139, 139, ppm).split("\n")[1]
    assert circle == "⌀ 139 px  =  625.5 µm", circle
    ellipse = _caption(wk_t.A_ROI_ELLIPSE, 254, 194, ppm).split("\n")[1]
    assert ellipse.startswith("axes 254 × 194 px  =  1.143 mm × 873 µm"), ellipse


def test_pixel_count_is_an_area():
    tail = _caption(wk_t.A_ROI_ELLIPSE, 139, 139).split("\n")[2]
    assert tail.split()[1] == "px²", tail


def test_ellipse_area_is_pi_a_b_not_the_box():
    for w, h in ((139.0, 139.0), (254.0, 194.0), (40.0, 12.0)):
        a = _annot(wk_t.A_ROI_ELLIPSE, w, h)
        count = int(wk_t._region_mask(a, (400, 600)).sum())
        want = math.pi * (w / 2.0) * (h / 2.0)
        # Whole pixels only: the thinner the shape, the coarser the staircase along
        # its edge, so a 12-pixel-tall ellipse cannot land closer than a few percent.
        tol = 0.02 if min(w, h) >= 100 else 0.04
        assert abs(count - want) / want < tol, (w, h, count, want)
        assert count < w * h * 0.85, "the area is being taken from the box"


def test_rectangle_area_is_the_box():
    a = _annot(wk_t.A_ROI_RECT, 100.0, 50.0)
    count = int(wk_t._region_mask(a, (400, 600)).sum())
    # The mask covers whole pixels, so a 100 × 50 box touches 101 × 51 of them.
    assert count == 101 * 51, count


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
    print("all ok")
