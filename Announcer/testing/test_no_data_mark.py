"""Readings that stop arriving: a red exclamation mark, and not one word.

Run:  python testing/test_no_data_mark.py

Asked for on 2026-09-21. The panel beside the circle used to carry a sentence
for every read that failed — "not read yet", "could not be read", "not
refreshed for 40 s" — and with the archiver having a bad minute that is a
column of red over the operator's own work which he can do nothing about. It
now says nothing at all about readings that did not arrive; instead, after five
minutes of them, a red exclamation mark appears beside the circle. Values that
are genuinely out of range still get their sentence, because that is the whole
point of the program.

What is checked here:

  * how long the readings have been failing (`WatchEngine.unread_for_s`), and
    that a channel written on change only — one that answers perfectly and has
    nothing in the window — is never counted as a failure;
  * that a row nobody has finished setting up does not pin the mark on;
  * that the panel's sentences carry warnings and trips and nothing else;
  * that the circle's own window shows and hides the mark and keeps the
    sentences off it.

No window is put on the screen: offscreen platform, and the one real window is
built with `WA_DontShowOnScreen` as the other tests here do it.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PySide6.QtCore import Qt                                  # noqa: E402
from PySide6.QtWidgets import QApplication                     # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_core as C                                           # noqa: E402
import ann_cpva as A                                           # noqa: E402

# The operator's own settings must never be in reach of a test.
_tmp = Path(tempfile.mkdtemp(prefix="announcer_nodata_"))
C.config_path = lambda: _tmp / "presets.json"

from main import AnnouncerWindow                               # noqa: E402

_win = AnnouncerWindow()
_win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
_win.show()
_app.processEvents()
_eng = _win.engine()


def _one_value(**kw):
    """One switched-on value on the list, and its state, fresh."""
    it = C.new_value_item(1, "helium", pv="PV:HE")
    it["on"] = True
    it.update(kw)
    items = _win.items()
    items[:] = [it]
    _win._preset = C.PRESET_ALL
    _win.refresh_tabs()
    _app.processEvents()
    st = _eng.state_of(1)
    st.since = time.monotonic()
    st.answered_at = time.monotonic()
    st.read_at = time.monotonic()
    st.state, st.text = C.STATE_OK, "42"
    return it, st


# ── how long the readings have been failing ─────────────────────────────────

def test_a_reading_that_arrives_is_not_a_failure():
    _one_value()
    age = _eng.unread_for_s()
    assert age is not None and age < 1.0, age
    assert not _eng.no_data(), "the mark came up while everything was read"


def test_five_minutes_of_failures_earns_the_mark():
    _it, st = _one_value()
    st.answered_at = time.monotonic() - (C.NO_DATA_S + 5)
    assert _eng.no_data(), "five minutes of failed reads said nothing"
    st.answered_at = time.monotonic() - (C.NO_DATA_S - 30)
    assert not _eng.no_data(), "four and a half minutes is not five"


def test_a_quiet_channel_is_never_no_data():
    """A channel written on change only answers perfectly and has nothing in
    the window. That is a healthy channel, not a dead one."""
    _it, st = _one_value()
    now = time.monotonic()
    st.answered_at = now                       # the archiver answered
    st.read_at = now - 3600                    # but there was no sample in it
    st.state, st.text = C.STATE_UNKNOWN, "nothing archived in the window"
    assert not _eng.no_data(), \
        "a quiet channel was reported as unreadable data"


def test_a_row_nobody_finished_setting_up_does_not_count():
    """No channel typed in, no rectangle drawn: not a reading that stopped."""
    _one_value(pv="")
    assert _eng.unread_for_s() is None, "an empty row was counted as a failure"
    it = C.new_area_item(2, "no rectangle")
    it["on"] = True
    _win.items().append(it)
    _win.refresh_tabs()
    _app.processEvents()
    assert _eng.unread_for_s() is None, "an undrawn area was counted"


def test_a_switched_off_row_does_not_count():
    _it, st = _one_value(on=False)
    st.answered_at = time.monotonic() - 10 * C.NO_DATA_S
    assert not _eng.no_data(), "a row that is switched off asked for the mark"


def test_the_error_of_a_failed_read_still_reaches_the_log():
    """The mark replaces the sentences on the PANEL, not the log line."""
    said = []
    _eng.logged.connect(lambda text, hint=None: said.append(text))
    _it, st = _one_value()
    _eng._value_done(_eng._value_gen,
                     {1: A.Reading(error="Archiver unavailable")})
    _app.processEvents()
    assert any("Archiver unavailable" in line for line in said), said


# ── what the panel says ─────────────────────────────────────────────────────

def test_the_panel_says_nothing_about_a_read_that_failed():
    _it, st = _one_value()
    for state, text in ((C.STATE_UNKNOWN, "could not be read"),
                        (C.STATE_UNKNOWN, "not read yet"),
                        (C.STATE_STALE, "not refreshed for 40 s")):
        st.state, st.text = state, text
        assert _win._badges() == [], \
            f'"{text}" was put over the operator\'s work'


def test_the_panel_still_says_a_value_out_of_range():
    _it, st = _one_value()
    st.state, st.text = C.STATE_TRIP, "56 is over 50"
    said = [text for text, _state in _win._badges()]
    assert said and "56 is over 50" in said[0], said


# ── the circle's own window ─────────────────────────────────────────────────

def test_the_circle_shows_and_hides_the_mark():
    hud = _win.ensure_hud()
    assert hud is not None
    hud.set_no_data(True)
    assert hud._mark_visible(), "the mark never appeared"
    hud.set_no_data(False)
    assert not hud._mark_visible(), "the mark stayed up"


def test_the_sentences_keep_off_the_mark():
    """Both are things that must not be covered: the sentences go elsewhere."""
    from PySide6.QtCore import QRect
    import ann_alarm as AL
    circle = QRect(1800, 20, 36, 36)          # top right, its usual home
    mark = QRect(1770, 20, 16, 34)            # to its left, no room right
    area = QRect(0, 0, 1920, 1080)
    x, y = AL.badge_spot(circle.united(mark), 360, 26, area)
    strip = QRect(x, y, 360, 26)
    assert not strip.intersects(circle), "a sentence landed on the circle"
    assert not strip.intersects(mark), "a sentence landed on the mark"


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
        except Exception as exc:                               # noqa: BLE001
            bad += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - bad} of {len(fns)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run_all())
