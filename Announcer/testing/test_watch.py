"""The engine: latching, groups, the one-shot alarm and the wedge watchdog.

Run:  python testing/test_watch.py

No network and no windows are needed, but a QApplication is: the engine is a
QObject with timers. The timers are stopped straight after it is built, and the
results are fed in by hand — that way each rule is checked on its own rather
than by waiting to see what happens.
"""
import os
import sys
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

from PySide6.QtWidgets import QApplication      # noqa: E402

_app = QApplication.instance() or QApplication([])

import ann_core as C                            # noqa: E402
import ann_cpva as A                            # noqa: E402
from ann_watch import WatchEngine               # noqa: E402


class Harness:
    """An engine with its clocks stopped and a list we control."""

    def __init__(self, items):
        self.items = items
        self.engine = WatchEngine(lambda: self.items)
        self.engine._value_timer.stop()
        self.engine._area_timer.stop()
        self.fired = []
        self.logged = []
        self.engine.fired.connect(lambda i, s: self.fired.append((i, s)))
        self.engine.logged.connect(lambda m, h: self.logged.append(m))

    def feed(self, readings):
        """Hand the engine one value pass's worth of results."""
        self.engine._value_gen += 1
        self.engine._value_inflight = True
        self.engine._value_done(self.engine._value_gen, readings)

    def state(self, iid):
        return self.engine.state_of(iid).state

    def close(self):
        self.engine.shutdown()


def reading(value):
    return A.Reading(value, A.now_ns(), 1)


def failed(msg="PV:X — Archiver unreachable"):
    return A.Reading(error=msg)


# ── one value, in and out of range ──────────────────────────────────────────

def test_a_value_in_range_is_ok_and_fires_nothing():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.engine.start()
        h.feed({1: reading(5.0)})
        assert h.state(1) == C.STATE_OK
        assert h.fired == []
    finally:
        h.close()


def test_a_value_over_its_limit_fires_once():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10,
                                  message="A is too high")])
    try:
        h.engine.start()
        h.feed({1: reading(11.0)})
        assert h.state(1) == C.STATE_TRIP
        assert len(h.fired) == 1
        assert h.fired[0] == (1, "A is too high")
        # Still over the limit on the next pass — and it must NOT fire again.
        # The alarm is one-shot; whoever listens stops the watch, and Reset is
        # what arms it.
        h.feed({1: reading(12.0)})
        assert len(h.fired) == 1
    finally:
        h.close()


def test_a_warning_never_fires():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi=5, hi_hi=10)])
    try:
        h.engine.start()
        h.feed({1: reading(7.0)})
        assert h.state(1) == C.STATE_WARN
        assert h.fired == []
    finally:
        h.close()


def test_show_only_never_flashes_but_still_colours_itself():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10, fires="show")])
    try:
        h.engine.start()
        h.feed({1: reading(11.0)})
        assert h.state(1) == C.STATE_TRIP
        assert h.fired == [], "a 'shows only' row must not raise the alarm"
        assert any("needs attention" in m or "PV:A" in m or "over" in m
                   for m in h.logged), "but it must still be said out loud once"
    finally:
        h.close()


def test_nothing_fires_while_not_watching():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.feed({1: reading(11.0)})
        assert h.state(1) == C.STATE_TRIP, "the reading is taken either way"
        assert h.fired == [], "but nothing is raised until watching is on"
    finally:
        h.close()


def test_a_switched_off_row_is_off_whatever_it_reads():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10, on=False)])
    try:
        h.engine.start()
        h.feed({1: reading(999.0)})
        assert h.state(1) == C.STATE_OFF
        assert h.fired == []
    finally:
        h.close()


# ── latching: said once, not twice a second ─────────────────────────────────

def test_a_trip_is_announced_once_and_again_after_it_recovers():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10, fires="show",
                                  message="A is too high")])
    try:
        h.feed({1: reading(11.0)})
        h.feed({1: reading(12.0)})
        h.feed({1: reading(13.0)})
        said = [m for m in h.logged if "A is too high" in m]
        assert len(said) == 1, f"said {len(said)} times, should be once"
        h.feed({1: reading(1.0)})            # back to normal
        h.feed({1: reading(14.0)})           # wrong again
        said = [m for m in h.logged if "A is too high" in m]
        assert len(said) == 2, "a second event is a second sentence"
    finally:
        h.close()


def test_reset_arms_it_again():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.engine.start()
        h.feed({1: reading(11.0)})
        assert len(h.fired) == 1
        h.engine.reset()
        h.engine.start()
        h.feed({1: reading(11.0)})
        assert len(h.fired) == 2, \
            "if it is still wrong after a Reset it fires again — that is the truth"
    finally:
        h.close()


# ── presets do not gate: each one fires on its own ──────────────────────────

def test_two_alarms_in_one_preset_do_not_wait_for_each_other():
    """The old group held its members back. A preset is a set, not a gate."""
    h = Harness([
        C.new_value_item(1, "a", "PV:A", hi_hi=10, presets=["pair"],
                         message="this one is wrong"),
        C.new_value_item(2, "b", "PV:B", hi_hi=10, presets=["pair"],
                         message="this one is wrong"),
    ])
    try:
        h.engine.start()
        h.feed({1: reading(11.0), 2: reading(1.0)})
        assert h.state(1) == C.STATE_TRIP and h.state(2) == C.STATE_OK
        assert len(h.fired) == 1, "the one that is wrong says so at once"
        h.feed({1: reading(11.0), 2: reading(11.0)})
        assert len(h.fired) == 2, "and so does the second, when it goes"
    finally:
        h.close()


# ── an unreadable value is never called fine ────────────────────────────────

def test_a_failed_read_is_unknown_and_said_out_loud():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.engine.start()
        h.feed({1: failed()})
        assert h.state(1) == C.STATE_UNKNOWN
        assert h.fired == [], "a reading that was never made cannot fire"
        assert any("unreachable" in m for m in h.logged)
    finally:
        h.close()


def test_an_empty_window_is_unknown_not_ok():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.feed({1: A.Reading(None, None, 0)})
        assert h.state(1) == C.STATE_UNKNOWN
        assert "nothing archived" in h.engine.state_of(1).text
    finally:
        h.close()


def test_a_value_that_stops_refreshing_says_so():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.feed({1: reading(5.0)})
        assert h.state(1) == C.STATE_OK
        # Pretend the last good read was long ago, then let the heartbeat look.
        h.engine.state_of(1).read_at = time.monotonic() - C.STALE_AFTER_S - 5
        h.engine._refresh_staleness(h.items)
        assert h.state(1) == C.STATE_STALE
        assert "not refreshed" in h.engine.state_of(1).text
    finally:
        h.close()


# ── the wedge watchdog ──────────────────────────────────────────────────────

def test_a_pass_that_never_returns_is_written_off():
    """The failure this guards against: one read that never comes back leaves a
    plain boolean guard set, and then nothing is ever read again."""
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.engine._value_inflight = True
        h.engine._value_started = time.monotonic() - 10_000
        gen_before = h.engine._value_gen
        h.engine._check_wedge("values", 0.5)
        assert h.engine._value_inflight is False, "the guard has to be cleared"
        assert h.engine._value_gen != gen_before, \
            "and the generation has to move on, so the late answer is dropped"
        assert any("writing it off" in m for m in h.logged), \
            "and it has to be said out loud, or the program looks calm"
    finally:
        h.close()


def test_a_late_answer_from_a_written_off_pass_is_dropped():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.engine.start()
        stale_gen = h.engine._value_gen
        h.engine._value_gen += 5            # the pass was written off meanwhile
        h.engine._value_done(stale_gen, {1: reading(11.0)})
        assert h.state(1) != C.STATE_TRIP
        assert h.fired == [], "a written-off pass must not raise anything"
    finally:
        h.close()


def test_a_wedge_is_not_declared_too_early():
    h = Harness([C.new_value_item(1, "a", "PV:A")])
    try:
        h.engine._value_inflight = True
        h.engine._value_started = time.monotonic() - 0.2
        h.engine._check_wedge("values", 0.5)
        assert h.engine._value_inflight is True, \
            "a pass that is merely busy must be left alone"
    finally:
        h.close()


# ── the engine says what cannot fire ────────────────────────────────────────

def test_it_names_what_can_never_fire():
    h = Harness([
        C.new_value_item(1, "no limits", "PV:A"),
        C.new_value_item(2, "warn only", "PV:B", hi=1),
        C.new_area_item(3, "no rectangle"),
        C.new_area_item(4, "no picture", region=[0, 0, 10, 10]),
        C.new_value_item(5, "fine", "PV:C", hi_hi=1),
    ])
    try:
        said = " | ".join(h.engine.reasons_it_cannot_fire())
        assert "no limits" in said and "warn only" in said
        assert "no rectangle" in said and "no picture" in said
        assert "fine" not in said
    finally:
        h.close()


def test_an_edited_away_item_does_not_crash_a_pass_in_flight():
    h = Harness([C.new_value_item(1, "a", "PV:A", hi_hi=10)])
    try:
        h.engine.start()
        h.items = []                  # deleted while the read was out
        h.feed({1: reading(11.0)})
        assert h.fired == []
    finally:
        h.close()


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
