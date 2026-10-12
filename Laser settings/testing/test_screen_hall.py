"""Laser settings — the valve boxes read off a picture of the L3BT MSS window.

    python testing/test_screen_hall.py

`mss_window.png` is the window as it was on 1.10.2026, cut to its frame: SGV502
open (beam into E2), SGV503 crossed out, SGV504 and SGV505 closed. Pillow is
needed here only to open the picture; the program itself does not use it.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PIL import Image, ImageOps                                  # noqa: E402

import ls_screen as S                                           # noqa: E402

FAILED = []

WANT = {"E2": S.OPEN, "E3": S.CLOSED, "E4": S.CLOSED, "E5": S.CLOSED}


def check(what, got, want):
    if got != want:
        FAILED.append(f"{what}: got {got!r}, wanted {want!r}")
        print(f"  FAIL  {what}: got {got!r}, wanted {want!r}")
    else:
        print(f"  ok    {what}")


def picture(img):
    """A PIL image as the BGRA bytes `grab_window` hands over."""
    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    bgra = Image.merge("RGBA", (b, g, r, a))
    return S.Picture(rgba.width, rgba.height, bgra.tobytes())


def window():
    return Image.open(HERE / "mss_window.png").convert("RGB")


def test_the_window_as_it_was():
    print("the window on 1.10.2026: beam into E2, the rest shut")
    got = S.read_picture(picture(window()), now_s=1000.0)
    check("positions", got.positions, WANT)
    check("nothing missing", got.problem, None)


def test_a_bigger_window():
    print("the same window at 125 % and 150 % (another DPI) reads the same")
    img = window()
    for factor in (1.25, 1.5):
        for how, name in ((Image.NEAREST, "sharp"), (Image.BILINEAR, "soft")):
            big = img.resize((round(img.width * factor),
                              round(img.height * factor)), how)
            got = S.read_picture(picture(big), now_s=1000.0)
            check(f"{factor:.2f}x {name}", got.positions, WANT)


def test_a_window_that_drifted():
    print("the contents moved a few pixels inside the window")
    img = window()
    for dx, dy in ((8, 0), (-8, 5), (0, -10), (12, 12)):
        moved = Image.new("RGB", img.size, (250, 250, 250))
        moved.paste(img, (dx, dy))
        got = S.read_picture(picture(moved), now_s=1000.0)
        check(f"moved by {dx}, {dy}", got.positions, WANT)


def _flip_box(img, box):
    """Swap the two halves of one box, outline kept: open <-> closed."""
    x0, y0, x1, y1 = box
    inner = img.crop((x0 + 1, y0 + 1, x1, y1))
    img.paste(ImageOps.mirror(inner), (x0 + 1, y0 + 1))
    return img


def test_open_and_closed_both_ways():
    print("SGV502 shut and SGV504 opened: the beam goes into E4")
    img = window()
    pic = picture(img)
    for hall in ("E2", "E4"):
        _valve, rx, ry = S.VALVE_BOXES[hall]
        box = S.find_box(pic, rx, ry, 1.0)
        check(f"{hall}'s box is found", box is not None, True)
        _flip_box(img, box)
    got = S.read_picture(picture(img), now_s=1000.0)
    check("positions", got.positions,
          {"E2": S.CLOSED, "E3": S.CLOSED, "E4": S.OPEN, "E5": S.CLOSED})


def test_nothing_to_read_is_no_answer():
    print("a blank window answers nothing, it does not say closed")
    blank = Image.new("RGB", (1190, 629), (250, 250, 250))
    got = S.read_picture(picture(blank), now_s=1000.0)
    check("no positions", got.positions, {})
    check("and it says which", "SGV502" in (got.problem or ""), True)

    print("a black picture (a window that drew nothing) is no answer either")
    black = Image.new("RGB", (1190, 629), (0, 0, 0))
    got = S.read_picture(picture(black), now_s=1000.0)
    check("no positions", got.positions, {})


def test_both_halves_black_is_no_answer():
    print("a box with both halves black is not believed")
    img = window()
    pic = picture(img)
    _valve, rx, ry = S.VALVE_BOXES["E2"]
    x0, y0, x1, y1 = S.find_box(pic, rx, ry, 1.0)
    img.paste((0, 0, 0), (x0, y0, x1 + 1, y1 + 1))
    got = S.read_picture(picture(img), now_s=1000.0)
    check("E2 not known", "E2" in got.positions, False)
    check("the others still are", got.positions,
          {"E3": S.CLOSED, "E4": S.CLOSED, "E5": S.CLOSED})


def main():
    for test in (test_the_window_as_it_was,
                 test_a_bigger_window,
                 test_a_window_that_drifted,
                 test_open_and_closed_both_ways,
                 test_nothing_to_read_is_no_answer,
                 test_both_halves_black_is_no_answer):
        test()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED")
        for line in FAILED:
            print("  " + line)
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
