"""The reference frame is read off a picture of the camera program's window.

Synthetic window pictures stand in for the camera program: a flat grey window
with a toolbar, a dark camera picture in it and a white reference outline on
the picture. What this checks (is_window_read.py, no Qt, no window opened):

* the camera picture is found inside the window, toolbar and status bar left out;
* the white outline is found and turned into the right fractions of the frame,
  within 0.5 %, for the PCW3_NF position measured by hand (344,166 -> 868,684
  on a 1024 x 1024 frame) and at other window scales;
* a saturated, filled beam spot is never taken for the frame — not beside the
  outline, not touching it;
* a 2 px thick outline and a white window background still work;
* a picture with no outline says so instead of inventing one;
* a hand-set picture area is used as given.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import is_window_read as W        # noqa: E402

FAILURES: "list[str]" = []

# PCW3_NF, measured by hand on a 1024 x 1024 frame.
FRAME = 1024
PCW3 = (344, 166, 868, 684)


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def window(pic_side=512, pic_xy=(40, 70), win=(900, 680), bg=(240, 240, 240),
           rect=PCW3, line=1, beam=None, frame_aspect=(1, 1)):
    """A window picture: grey window, toolbar, camera picture, white outline.

    `rect` is in FRAME pixels; it is scaled to the picture's size in the window,
    which is how the camera program draws it. `beam` is (cx, cy, r) in frame
    pixels for a filled white spot."""
    ww, wh = win
    img = np.empty((wh, ww, 3), np.uint8)
    img[:] = bg
    img[0:30, :] = (60, 90, 160)            # title / toolbar band
    img[wh - 22:, :] = (210, 210, 210)      # status bar
    img[40:60, 600:880] = (255, 255, 255)   # a white text box in the side panel
    px, py = pic_xy
    pw = pic_side
    ph = int(round(pic_side * frame_aspect[1] / frame_aspect[0]))
    rng = np.random.default_rng(1)
    pic = rng.integers(0, 40, (ph, pw, 3)).astype(np.uint8)
    pic[:, :, 1:] //= 3                      # dark reddish camera noise
    s = pw / FRAME
    if beam is not None:
        cx, cy, r = beam
        yy, xx = np.mgrid[0:ph, 0:pw]
        pic[(xx - cx * s) ** 2 + (yy - cy * s) ** 2 <= (r * s) ** 2] = 255
    if rect is not None:
        l, t, r_, b = (int(round(v * s)) for v in rect)
        pic[t:t + line, l:r_ + line] = 255
        pic[b:b + line, l:r_ + line] = 255
        pic[t:b + line, l:l + line] = 255
        pic[t:b + line, r_:r_ + line] = 255
    img[py:py + ph, px:px + pw] = pic
    return img, (px, py, px + pw, py + ph)


def close(fr, want, tol=0.005):
    return fr is not None and all(abs(a - b) <= tol for a, b in zip(fr, want))


def fmt(fr):
    return "None" if fr is None else "(" + ", ".join(f"{v:.4f}" for v in fr) + ")"


WANT = tuple(v / FRAME for v in PCW3)


def test_plain():
    print("PCW3 position, picture shown at half size:")
    img, area = window()
    found = W.find_picture_area(img, FRAME, FRAME)
    check("picture area found exactly", found == area, f"{found} vs {area}")
    res = W.read_image(img, FRAME, FRAME)
    check("no problem reported", res.problem is None, str(res.problem))
    check("fractions match the measured square", close(res.fractions, WANT),
          f"{fmt(res.fractions)} vs {fmt(WANT)}")


def test_scales():
    print("Other window scales:")
    for side in (300, 700, 1024):
        img, _ = window(pic_side=side, win=(side + 400, side + 140))
        res = W.read_image(img, FRAME, FRAME)
        check(f"picture {side} px", close(res.fractions, WANT, 0.005 + 1.0 / side),
              fmt(res.fractions))


def test_beam_beside_and_touching():
    print("Saturated beam spot:")
    img, _ = window(beam=(200, 850, 70))
    res = W.read_image(img, FRAME, FRAME)
    check("spot beside the frame ignored", close(res.fractions, WANT), fmt(res.fractions))
    img, _ = window(beam=(600, 166, 60))
    res = W.read_image(img, FRAME, FRAME)
    check("spot on the top line ignored", close(res.fractions, WANT), fmt(res.fractions))
    img, _ = window(beam=(600, 420, 120))
    res = W.read_image(img, FRAME, FRAME)
    check("spot inside the frame ignored", close(res.fractions, WANT), fmt(res.fractions))


def test_thick_and_white_window():
    print("Thick line, white window:")
    img, _ = window(line=2)
    res = W.read_image(img, FRAME, FRAME)
    want2 = tuple((v + 1.0) / FRAME for v in PCW3)     # 2 px line at half size
    check("2 px outline", close(res.fractions, want2, 0.006), fmt(res.fractions))
    img, area = window(bg=(255, 255, 255))
    res = W.read_image(img, FRAME, FRAME)
    check("white window background", close(res.fractions, WANT),
          f"{fmt(res.fractions)} area {res.area} vs {area}")


def test_not_square_frame():
    print("A 4:3 camera:")
    rect43 = (100, 80, 700, 500)
    img, area = window(pic_side=640, frame_aspect=(4, 3), rect=rect43, win=(1000, 640))
    res = W.read_image(img, 1024, 768)
    want = (100 / 1024, 80 / 768, 700 / 1024, 500 / 768)
    check("4:3 area found", res.area == area, f"{res.area} vs {area}")
    check("4:3 fractions", close(res.fractions, want), fmt(res.fractions))
    res = W.read_image(img, FRAME, FRAME)
    check("wrong aspect is not taken for the picture", res.area is None, str(res.area))


def test_big_black_picture():
    print("Picture larger than the window's background:")
    img, area = window(pic_side=600, win=(700, 700))
    img[70:670, 40:640] = np.where(img[70:670, 40:640] == 255, 255, 0)   # flat black
    res = W.read_image(img, FRAME, FRAME)
    check("background still recognised", res.area == area, f"{res.area} vs {area}")
    check("fractions", close(res.fractions, WANT, 0.006), fmt(res.fractions))


def test_nothing():
    print("No outline:")
    img, _ = window(rect=None, beam=(500, 500, 80))
    res = W.read_image(img, FRAME, FRAME)
    check("no frame invented", res.fractions is None, fmt(res.fractions))
    check("says why", bool(res.problem), str(res.problem))


def test_hand_area():
    print("Area set by hand:")
    img, area = window()
    res = W.read_image(img, None, None, area=area)
    check("hand area used", res.area == area, str(res.area))
    check("fractions from hand area", close(res.fractions, WANT), fmt(res.fractions))


def main():
    for t in (test_plain, test_scales, test_beam_beside_and_touching,
              test_thick_and_white_window, test_not_square_frame, test_big_black_picture,
              test_nothing, test_hand_area):
        t()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("   ", f)
        sys.exit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main()
