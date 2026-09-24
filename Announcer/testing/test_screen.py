"""The screens: the coordinate boundary, and that a grab returns what was asked.

Run:  python testing/test_screen.py

Rendered on the real Windows platform plugin — the offscreen one has no fonts
and no screens worth asking about. It takes real screenshots of whatever is on
the monitors; nothing is saved and nothing is clicked.

The test that matters is `test_a_grab_returns_exactly_the_rectangle_asked_for`,
run once per screen INCLUDING the one at 150 % scaling. A rectangle handed to
PIL in the wrong space comes back the wrong size, or — worse — the right size
from the wrong place, which looks like success for ever.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QPoint, QRect        # noqa: E402
from PySide6.QtWidgets import QApplication      # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_screen as S                          # noqa: E402


# ── the two spaces ──────────────────────────────────────────────────────────

def test_there_is_at_least_one_screen():
    assert S.screens(), "no screens at all — nothing below can mean anything"


def test_a_screen_origin_is_the_same_number_in_both_spaces():
    """Everything in this module rests on this, so it is checked, not assumed."""
    for s in S.screens():
        assert s.physical.topLeft() == s.logical.topLeft(), \
            f"{s.name}: physical {s.physical} vs logical {s.logical}"


def test_a_screen_physical_size_is_its_logical_size_times_the_ratio():
    for s in S.screens():
        assert s.physical.width() == round(s.logical.width() * s.dpr)
        assert s.physical.height() == round(s.logical.height() * s.dpr)


def test_the_conversion_round_trips_on_every_screen():
    for s in S.screens():
        for frac in (0.0, 0.25, 0.5, 0.99):
            p = QPoint(int(s.logical.x() + s.logical.width() * frac),
                       int(s.logical.y() + s.logical.height() * frac))
            back = S.to_logical(S.to_physical(p, s), s)
            assert abs(back.x() - p.x()) <= 1 and abs(back.y() - p.y()) <= 1, \
                f"{s.name}: {p} → {S.to_physical(p, s)} → {back}"


def test_a_scaled_screen_really_does_scale():
    """If nothing on this PC is scaled the test says so rather than passing."""
    scaled = [s for s in S.screens() if s.dpr != 1.0]
    if not scaled:
        print("      (no scaled screen on this PC — nothing to prove here)")
        return
    s = scaled[0]
    mid_logical = QPoint(s.logical.x() + s.logical.width() // 2,
                         s.logical.y() + s.logical.height() // 2)
    mid_physical = S.to_physical(mid_logical, s)
    assert mid_physical != mid_logical, \
        "a scaled screen must not map a point to itself"


def test_a_point_is_found_on_its_own_screen():
    for s in S.screens():
        mid = QPoint(s.physical.x() + s.physical.width() // 2,
                     s.physical.y() + s.physical.height() // 2)
        found = S.screen_for_physical(mid)
        assert found is not None and found.name == s.name


def test_a_point_on_no_screen_is_not_invented():
    assert S.screen_for_physical(QPoint(-99999, -99999)) is None


def test_a_region_belongs_to_the_screen_it_covers_most_of():
    infos = S.screens()
    s = infos[0]
    p = s.physical
    region = [p.x() + 10, p.y() + 10, p.x() + 110, p.y() + 110]
    found = S.screen_for_region(region, infos)
    assert found is not None and found.name == s.name
    assert S.screen_for_region(None) is None


# ── keeping a window on a screen ────────────────────────────────────────────

def test_fit_rect_keeps_a_window_on_its_own_screen():
    infos = S.screens()
    if len(infos) < 2:
        return
    s = infos[-1]
    a = s.available
    x, y = S.fit_rect(a.x() + 20, a.y() + 20, 300, 200, infos)
    assert s.available.contains(QRect(x, y, 300, 200)), \
        "a window on the second monitor must stay on the second monitor"


def test_fit_rect_pulls_a_window_back_from_nowhere():
    infos = S.screens()
    x, y = S.fit_rect(999_999, 999_999, 400, 300, infos)
    assert S.screen_for_physical(QPoint(x, y), infos) is not None or \
        any(s.available.contains(QPoint(x, y)) for s in infos)


def test_an_oversized_window_is_pinned_top_left_not_pushed_off():
    """The clamping order. `min` before `max`, or the title bar ends up
    somewhere nobody can reach it."""
    infos = S.screens()
    s = next((i for i in infos if i.primary), infos[0])
    a = s.available
    x, y = S.fit_rect(a.x() + 50, a.y() + 50,
                      a.width() + 800, a.height() + 800, infos)
    assert (x, y) == (a.x(), a.y())


# ── grabbing ────────────────────────────────────────────────────────────────

def test_a_grab_returns_exactly_the_rectangle_asked_for():
    """One per screen, the 150 % one included. This is the whole DPI question."""
    for s in S.screens():
        p = s.physical
        w, h = 240, 160
        region = [p.x() + p.width() // 2 - w // 2,
                  p.y() + p.height() // 2 - h // 2,
                  p.x() + p.width() // 2 + w // 2,
                  p.y() + p.height() // 2 + h // 2]
        img, err = S.grab_rect(region)
        assert err is None, f"{s.name}: {err}"
        assert img.size == (w, h), \
            (f"{s.name} (dpr {s.dpr}): asked for {w}x{h} at "
             f"{region[0]},{region[1]} and got {img.size}")


def test_a_grab_across_the_negative_top_edge_works():
    """This PC's virtual desktop starts at y = -174, and PIL works the offset
    out itself — no manual correction anywhere."""
    top = min(s.physical.y() for s in S.screens())
    if top >= 0:
        return
    s = next(s for s in S.screens() if s.physical.y() == top)
    region = [s.physical.x() + 10, top, s.physical.x() + 110, top + 100]
    img, err = S.grab_rect(region)
    assert err is None and img.size == (100, 100), f"{err or img.size}"


def test_a_rectangle_with_no_area_is_refused_not_grabbed():
    img, err = S.grab_rect([10, 10, 10, 10])
    assert img is None and "no area" in err
    img, err = S.grab_rect(None)
    assert img is None and err


# ── comparing ───────────────────────────────────────────────────────────────

def test_a_picture_compared_with_itself_is_zero():
    from PIL import Image
    a = Image.new("RGB", (20, 20), (10, 120, 200))
    assert S.picture_diff(a, a) == 0.0


def test_the_difference_is_the_mean_absolute_deviation():
    from PIL import Image
    a = Image.new("RGB", (10, 10), (0, 0, 0))
    b = Image.new("RGB", (10, 10), (30, 30, 30))
    assert abs(S.picture_diff(a, b) - 30.0) < 1e-6
    # A small change in a big rectangle averages away — which is exactly why
    # the advice is to draw the rectangle tight around the thing that matters.
    c = a.copy()
    c.putpixel((0, 0), (255, 255, 255))
    assert S.picture_diff(a, c) < 3.0


def test_two_different_sizes_cannot_be_compared():
    from PIL import Image
    a = Image.new("RGB", (10, 10))
    b = Image.new("RGB", (11, 10))
    assert S.picture_diff(a, b) is None


def test_a_grabbed_picture_survives_the_trip_to_qt():
    from PIL import Image
    img = Image.new("RGB", (40, 30), (200, 30, 30))
    pm = S.pil_to_qpixmap(img)
    assert not pm.isNull() and (pm.width(), pm.height()) == (40, 30)
    small = S.scaled_pixmap(img, 20, 20)
    assert small.width() <= 20 and small.height() <= 20
    same = S.scaled_pixmap(img, 400, 400)
    assert same.width() == 40, "a small picture is not blown up"


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
    for s in S.screens():
        print(f"    {s.name:18} logical={s.logical.x()},{s.logical.y()} "
              f"{s.logical.width()}x{s.logical.height()}  dpr={s.dpr}  "
              f"physical={s.physical.width()}x{s.physical.height()}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
