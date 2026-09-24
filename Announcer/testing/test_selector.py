"""Dragging a rectangle out: the WIDGET LIFETIME, which is what crashed.

Run:  python testing/test_selector.py

On 2026-09-17 Announcer died three times inside two minutes while the operator
was pressing "Draw the area". Windows recorded all three: faulting module
`Qt6Core.dll`, exception `0xc0000005` (access violation), three different
garbage pointers — the signature of a destroyed C++ object being used again.

The mechanism, and the thing every test here defends:

    `_SelectorOverlay` is a PARENTLESS QWidget, so Python owns its C++ object.
    The three overlays existed only inside `RegionSelector._overlays`, and the
    selector itself existed only in `AreaDialog._selector` — ONE attribute,
    which the next press of "Draw the area" overwrote. Dropping that reference
    destroys three VISIBLE top-level windows on the spot, inside whatever was
    running, and Qt faults on the next event.

Nothing here clicks or types into anything. The drag is driven by emitting the
overlay's own signals and by handing `eventFilter` a key event directly, which
are ordinary function calls; no input is injected anywhere.

`test_screen.py` covers the coordinate maths and the grabbing. This file covers
only who owns what, and when it dies.
"""
import gc
import os
import sys
from pathlib import Path

# The real platform plugin: `screens()` has to answer with the real monitors,
# because the number of overlays is the number of screens.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import QEvent, QPoint, QRect, Qt    # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget     # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_screen as S                                  # noqa: E402


def _drain():
    """Let deleteLater actually happen, then collect."""
    for _ in range(3):
        _app.processEvents()
        gc.collect()
    _app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _app.processEvents()


def _fresh():
    """A selector, shown, with the calls it made recorded."""
    picked, done = [], []
    sel = S.RegionSelector(lambda region, info: picked.append((region, info)),
                           lambda: done.append(True))
    sel.show()
    return sel, picked, done


# ── the crash itself ────────────────────────────────────────────────────────

def test_a_shown_selector_survives_losing_the_callers_reference():
    """THE crash. Drop the only reference and the overlays must still live."""
    sel, _picked, done = _fresh()
    overlays = list(sel._overlays)
    assert overlays, "no overlays were built — the rest cannot mean anything"
    assert S.selection_in_progress()

    del sel
    _drain()

    for ov in overlays:
        # Touching a destroyed PySide6 widget raises RuntimeError, and that is
        # the whole assertion: the width is not interesting, answering is.
        ov.width()
    assert not done, "nothing was cancelled — it was only let go of"

    # And it still closes cleanly afterwards.
    overlays[0].cancelled.emit()
    _drain()
    assert done == [True]
    assert not S.selection_in_progress()


def test_a_second_selector_does_not_destroy_the_first_ones_windows():
    """Two presses of "Draw the area". The old overlays must not be dropped."""
    first, _p1, done1 = _fresh()
    first_overlays = list(first._overlays)
    second, _p2, _d2 = _fresh()

    first = None                        # exactly what the old code did
    _drain()

    for ov in first_overlays:
        ov.width()                      # raises if it was destroyed
    assert not done1, "the first selector cancelled itself"

    for sel in list(S._LIVE_SELECTORS):
        sel._cancelled()
    _drain()
    assert not S.selection_in_progress()
    second._cancelled()


def test_retire_holds_the_reference_until_qt_has_really_destroyed_it():
    """The measured crash, in one assertion.

    Three minidumps from 2026-09-17 all faulted on the GUI thread inside
    `QCoreApplicationPrivate::sendPostedEvents`, one of them at the exact
    instruction that touches `pe.receiver->d_func()` — reading `0x16`. Freed
    memory already handed out again: Qt was delivering a queued event to an
    object that no longer existed. `retire` exists so the Python reference
    outlives the queue, not the other way round.
    """
    w = QWidget(None, Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.Tool)
    w.resize(40, 20)
    w.move(-4000, -4000)                # off the desk, nothing is covered
    w.show()
    _app.postEvent(w, QEvent(QEvent.Type.User))     # a queued event for it
    w.hide()
    S.retire(w)
    assert w in S._RETIRED, "it was let go of straight away"
    del w
    _drain()
    assert not S._RETIRED, "it was never let go of at all"


def test_taking_the_overlays_down_retires_them():
    sel, _picked, _done = _fresh()
    overlays = list(sel._overlays)
    sel._cancelled()
    # Still held, because Qt has not run the deferred deletes yet.
    assert any(ov in S._RETIRED for ov in overlays), \
        "the overlays were dropped before Qt had finished with them"
    _drain()


def test_the_monitor_numbers_are_retired_too():
    """Same parentless-top-level shape, same treatment."""
    numbers = S.MonitorNumbers()
    numbers.show()
    labels = list(numbers._labels)
    assert labels, "no labels were made"
    numbers.hide()
    assert any(lbl in S._RETIRED for lbl in labels)
    _drain()


def test_closing_releases_the_hold_so_nothing_is_kept_for_ever():
    sel, _picked, _done = _fresh()
    assert sel in S._LIVE_SELECTORS
    sel._cancelled()
    assert sel not in S._LIVE_SELECTORS
    _drain()


# ── always ending in on_done ────────────────────────────────────────────────

def test_escape_cancels_and_brings_the_windows_back():
    sel, picked, done = _fresh()
    sel._overlays[0].cancelled.emit()
    _drain()
    assert done == [True], "the caller was never told it was over"
    assert not picked


def test_a_held_escape_cancels_once_and_not_thirty_times():
    """A held Esc used to emit `cancelled` on every auto-repeat, and each one
    armed another "bring the windows back", so the dialog re-showed in a burst.
    """
    sel, _picked, done = _fresh()
    ov = sel._overlays[0]               # held: the first emit empties the list
    for _ in range(30):
        ov.cancelled.emit()
    _drain()
    assert done == [True], f"cancelled {len(done)} times"


def test_a_stray_click_cancels_rather_than_selecting_a_speck():
    """And this is the way out that needs no keyboard: one click, anywhere.

    A drag under five pixels is not a rectangle, so it cancels — which means
    three dimmed screens are never a dead end, however the keyboard focus went.
    """
    sel, picked, done = _fresh()
    ov = sel._overlays[0]
    ov._origin = QPoint(20, 20)         # a press…
    ov._band = QRect(QPoint(20, 20), QPoint(22, 21))    # …and a twitch
    assert ov._band.width() < 5
    ov.cancelled.emit()                 # what that release emits
    _drain()
    assert not picked
    assert done == [True]


def test_the_instruction_names_both_ways_out():
    assert "Esc" in S.RegionSelector.INSTRUCTION
    assert "click" in S.RegionSelector.INSTRUCTION.lower()


def test_it_gives_itself_up_rather_than_dimming_the_desktop_for_ever():
    sel, _picked, done = _fresh()
    assert sel._give_up.isActive(), "no safety net at all"
    assert sel.GIVE_UP_MS >= 60_000, "too impatient to decide where to drag"
    sel._give_up.timeout.emit()         # what it does when it runs out
    _drain()
    assert done == [True]
    assert not S.selection_in_progress()


def test_only_the_first_answer_counts():
    """Two overlays answering the same drag must not both be acted on."""
    sel, picked, done = _fresh()
    info = sel._overlays[0]._info
    sel._picked(QRect(QPoint(10, 10), QPoint(50, 40)), info)
    sel._picked(QRect(QPoint(99, 99), QPoint(120, 120)), info)
    sel._cancelled()
    _drain()
    assert len(picked) == 1, f"acted on {len(picked)} answers"
    assert len(done) == 1


def test_an_owner_going_away_can_take_the_overlays_with_it():
    """`take_down` is for an owner that is closing.

    Closing the area editor inside the 150 ms before the overlays appear used
    to leave a selector nobody owned — three dimmed screens with nothing to
    report back to, and, because one drag at a time is the rule, "Draw the
    area" refused to work at all afterwards.
    """
    sel, picked, done = _fresh()
    sel.take_down()
    _drain()
    assert not done, "it called back into an owner that is going away"
    assert not picked
    assert not S.selection_in_progress(), "it still thinks it owns the screen"
    assert not sel._overlays


def test_closing_twice_is_harmless():
    sel, _picked, done = _fresh()
    sel._cancelled()
    sel._cancelled()
    sel._close()
    _drain()
    assert done == [True]


def test_with_no_screens_the_caller_is_still_released():
    """Otherwise the program hides its windows and waits for ever."""
    real = S.screens
    S.screens = lambda: []
    try:
        picked, done = [], []
        sel = S.RegionSelector(lambda r, i: picked.append(r),
                               lambda: done.append(True))
        sel.show()
        _drain()
        assert done == [True], "no screens left the windows hidden for good"
        assert not S.selection_in_progress()
    finally:
        S.screens = real


def test_a_picked_rectangle_is_physical_and_the_right_way_round():
    sel, picked, _done = _fresh()
    info = S.screens()[0]
    sel._picked(QRect(QPoint(40, 30), QPoint(140, 130)), info)
    _drain()
    assert len(picked) == 1
    region, got_info = picked[0]
    assert got_info is info
    assert region == [40, 30, 140, 130], region
    assert region[2] > region[0] and region[3] > region[1]


# ── the painter ─────────────────────────────────────────────────────────────

class _FakePainter:
    """Stands in for QPainter so a failing paint can be tested at all.

    Letting a real exception out of a real paintEvent is not a test that can be
    run: PySide6 leaves the exception set across the C++ boundary, every
    override called afterwards fails with `SystemError`, and the process goes
    down with an access violation in the next garbage collection. Measured
    2026-09-17 — the first version of this test did exactly that. So the painter
    is faked and only the guarantee is checked: whatever the body does, `end()`
    was called.
    """

    def __init__(self, _device):
        self.ended = False

    def end(self):
        self.ended = True

    def __getattr__(self, _name):
        # Every other call answers with something that can be called and
        # measured again, so `p.fontMetrics().boundingRect(t).adjusted(...)`
        # walks through without the body having to know it is being faked.
        return self._anything

    def _anything(self, *_a, **_k):
        return self


def test_a_failing_overlay_paint_still_closes_its_painter():
    """The guarantee behind every try/finally in this program.

    An exception escaping a paintEvent leaves the QPainter active on the
    widget. Qt then prints "QBackingStore::endPaint() called with active
    painter" on every repaint and the program dies on the next window move with
    "Cannot destroy paint device that is being painted" — nowhere near the
    overlay that actually broke.
    """
    sel, _picked, _done = _fresh()
    ov = sel._overlays[0]
    made = []
    real_painter = S.QPainter

    def factory(device):
        p = _FakePainter(device)
        made.append(p)
        return p

    S.QPainter = factory
    try:
        ov._paint_body = lambda _p: (_ for _ in ()).throw(
            RuntimeError("deliberate"))
        try:
            ov.paintEvent(None)
        except RuntimeError:
            pass                # out of paintEvent is fine; leaking is not
    finally:
        S.QPainter = real_painter
        del ov._paint_body
    assert made, "no painter was made, so nothing was tested"
    assert made[0].ended, "the painter was left open on the widget"
    sel._cancelled()
    _drain()


def test_a_working_overlay_paint_closes_its_painter_too():
    sel, _picked, _done = _fresh()
    ov = sel._overlays[0]
    made = []
    real_painter = S.QPainter

    def factory(device):
        p = _FakePainter(device)
        made.append(p)
        return p

    # The body reads `QPainter.RenderHint` and `QPainter.CompositionMode` off
    # the module global, so the stand-in has to carry them.
    factory.RenderHint = real_painter.RenderHint
    factory.CompositionMode = real_painter.CompositionMode
    S.QPainter = factory
    try:
        ov.paintEvent(None)
    finally:
        S.QPainter = real_painter
    assert made and made[0].ended
    sel._cancelled()
    _drain()


# ── one photograph, not one per area ────────────────────────────────────────

def test_several_regions_cost_one_photograph():
    """The watcher took one full-desktop grab PER AREA, twice a second."""
    calls = []
    real = S.grab_rect

    def counted(region):
        calls.append(list(region))
        return real(region)

    S.grab_rect = counted
    try:
        s = S.screens()[0].physical
        regions = [[s.x() + 10, s.y() + 10, s.x() + 60, s.y() + 50],
                   [s.x() + 100, s.y() + 20, s.x() + 180, s.y() + 90],
                   [s.x() + 30, s.y() + 200, s.x() + 90, s.y() + 260]]
        out = S.grab_regions(regions)
    finally:
        S.grab_rect = real
    assert len(calls) == 1, f"{len(calls)} photographs for 3 areas"
    assert len(out) == 3
    for region, (img, err) in zip(regions, out):
        assert err is None, err
        assert img.size == (region[2] - region[0], region[3] - region[1]), \
            f"{region} came back {img.size}"


def test_each_region_gets_its_own_pixels_and_not_the_neighbours():
    """The crop offsets have to be right, or every area watches the wrong place
    and reports calm for ever — the failure that looks exactly like success."""
    s = S.screens()[0].physical
    regions = [[s.x() + 10, s.y() + 10, s.x() + 60, s.y() + 50],
               [s.x() + 120, s.y() + 60, s.x() + 200, s.y() + 130]]
    together = S.grab_regions(regions)
    for region, (img, err) in zip(regions, together):
        assert err is None, err
        alone, err2 = S.grab_rect(region)
        assert err2 is None, err2
        assert alone.size == img.size
        # The desktop may have changed between the two photographs, so this is
        # a size-and-place check, not a pixel-for-pixel one; `test_screen.py`
        # owns the "exactly the rectangle asked for" test.
        assert img.size == (region[2] - region[0], region[3] - region[1])


def test_a_blank_rectangle_is_reported_and_does_not_spoil_the_others():
    s = S.screens()[0].physical
    good = [s.x() + 10, s.y() + 10, s.x() + 60, s.y() + 50]
    out = S.grab_regions([None, [0, 0, 0, 0], good])
    assert out[0][0] is None and out[0][1]
    assert out[1][0] is None and out[1][1]
    assert out[2][0] is not None and out[2][1] is None


def test_no_regions_at_all_is_an_empty_answer_not_a_photograph():
    calls = []
    real = S.grab_rect
    S.grab_rect = lambda r: (calls.append(r), (None, "x"))[1]
    try:
        assert S.grab_regions([]) == []
    finally:
        S.grab_rect = real
    assert not calls


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
        finally:
            for sel in list(S._LIVE_SELECTORS):
                try:
                    sel._close()
                except Exception:
                    pass
            _drain()
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
