"""Where the strip of sentences goes, and where it must never go.

Run:  python testing/test_hud.py

No windows are shown: the placement is a pure function over rectangles, which
is exactly why it was pulled out of the widget. The bug it is about was
photographed by the operator on 2026-09-17 — six red badges sitting squarely on
top of the green circle, which is the one thing on screen that has to stay
visible.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QRect                # noqa: E402
from PySide6.QtWidgets import QApplication      # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_alarm as AL                          # noqa: E402

# A screen like the operator's main one, and the circle where it parks itself:
# the top right corner, 24 px in.
SCREEN = QRect(0, 0, 1280, 672)
CIRCLE = QRect(SCREEN.width() - 36 - 24, 24, 36, 36)
WIDE, TALL = 630, 190          # the strip in the photograph: six badges


def spot(circle=CIRCLE, w=WIDE, h=TALL, area=SCREEN):
    x, y = AL.badge_spot(circle, w, h, area)
    return QRect(x, y, w, h)


def test_the_strip_never_covers_the_circle():
    assert not spot().intersects(CIRCLE)


def test_in_the_top_right_corner_it_goes_to_the_left():
    """There is no room to the right, and that is the whole bug."""
    r = spot()
    assert r.right() <= CIRCLE.left(), f"{r} is not left of {CIRCLE}"
    assert SCREEN.contains(r)


def test_with_room_to_the_right_it_goes_to_the_right():
    circle = QRect(40, 24, 36, 36)
    r = spot(circle=circle)
    assert r.left() >= circle.right(), f"{r} is not right of {circle}"
    assert not r.intersects(circle)


def test_a_strip_wider_than_the_screen_goes_underneath():
    r = spot(w=SCREEN.width() + 400)
    assert not r.intersects(CIRCLE)
    assert r.top() >= CIRCLE.bottom(), "underneath is the only safe direction"


def test_the_circle_in_every_corner_and_the_middle():
    """Wherever it has been dragged, the sentences stay off it."""
    places = {
        "top left": QRect(8, 8, 36, 36),
        "top right": QRect(SCREEN.width() - 44, 8, 36, 36),
        "bottom left": QRect(8, SCREEN.height() - 44, 36, 36),
        "bottom right": QRect(SCREEN.width() - 44, SCREEN.height() - 44, 36, 36),
        "middle": QRect(SCREEN.width() // 2, SCREEN.height() // 2, 36, 36),
        "hard against the edge": QRect(SCREEN.width() - 36, 0, 36, 36),
    }
    for name, circle in places.items():
        for w, h in ((WIDE, TALL), (200, 40), (SCREEN.width() + 100, 40),
                     (300, SCREEN.height() + 100)):
            r = spot(circle=circle, w=w, h=h)
            assert not r.intersects(circle), \
                f"circle {name}, strip {w}x{h}: {r} sits on {circle}"


def test_no_screen_at_all_still_avoids_the_circle():
    r = spot(area=None)
    assert not r.intersects(CIRCLE)


def test_one_badge_beside_the_circle_fits_on_the_screen():
    r = spot(w=320, h=34)
    assert SCREEN.contains(r) and not r.intersects(CIRCLE)


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok    {name}")
        except AssertionError as exc:
            bad += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:
            bad += 1
            print(f"  ERROR {name}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
