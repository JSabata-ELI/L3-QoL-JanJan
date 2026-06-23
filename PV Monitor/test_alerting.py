"""Unit tests for the alert state machine (no Qt, no network)."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from alerting import (  # noqa: E402
    AlertEvaluator, AlertLevel, AlertState, EvalConfig, Thresholds,
)

SEC = 1_000_000_000
MIN = 60 * SEC

# Symmetric thresholds around ~10: warn at 8/12, alarm at 5/15.
THR = Thresholds(warn_low=8.0, warn_high=12.0, alarm_low=5.0, alarm_high=15.0)


def feed(ev, state, values, thr=THR, start=0, step=SEC):
    """Feed a sequence of values; return list of (level, notification)."""
    out = []
    t = start
    for v in values:
        note = ev.evaluate(state, v, thr, t)
        out.append((state.level, note))
        t += step
    return out


def test_stays_ok_when_inside():
    ev = AlertEvaluator(EvalConfig(debounce_count=1))
    res = feed(ev, AlertState(), [10.0, 9.5, 11.0])
    assert all(lvl == AlertLevel.OK and n is None for lvl, n in res)


def test_warning_transition_with_debounce():
    ev = AlertEvaluator(EvalConfig(debounce_count=2))
    st = AlertState()
    # One excursion poll: pending, not yet committed.
    n1 = ev.evaluate(st, 13.0, THR, 0)
    assert st.level == AlertLevel.OK and n1 is None
    # Second consecutive excursion: commit + notify.
    n2 = ev.evaluate(st, 13.0, THR, SEC)
    assert st.level == AlertLevel.WARNING
    assert n2 is not None and n2.level == AlertLevel.WARNING
    assert n2.kind == "transition"


def test_debounce_resets_on_return():
    ev = AlertEvaluator(EvalConfig(debounce_count=3))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)        # pending warn (1)
    ev.evaluate(st, 10.0, THR, SEC)      # back inside -> pending cleared
    assert st.level == AlertLevel.OK and st.pending_count == 0


def test_escalation_to_alarm():
    ev = AlertEvaluator(EvalConfig(debounce_count=1))
    st = AlertState()
    feed(ev, st, [13.0])                 # -> WARNING
    assert st.level == AlertLevel.WARNING
    n = ev.evaluate(st, 16.0, THR, 5 * SEC)
    assert st.level == AlertLevel.ALARM
    assert n is not None and n.prev_level == AlertLevel.WARNING


def test_hysteresis_blocks_immediate_recovery():
    # span = 12-8 = 4; deadband = 0.25*4 = 1.0 -> recover only below 11.0
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.25))
    st = AlertState()
    feed(ev, st, [13.0])                 # WARNING
    assert st.level == AlertLevel.WARNING
    n = ev.evaluate(st, 11.5, THR, 2 * SEC)   # inside raw, but within deadband
    assert st.level == AlertLevel.WARNING and n is None
    n = ev.evaluate(st, 10.5, THR, 3 * SEC)   # clears deadband -> recover
    assert st.level == AlertLevel.OK
    assert n is not None and n.level == AlertLevel.OK


def test_recovery_notify_can_be_disabled():
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.0,
                                   recovery_notify=False))
    st = AlertState()
    feed(ev, st, [13.0])                 # WARNING
    n = ev.evaluate(st, 10.0, THR, 2 * SEC)
    assert st.level == AlertLevel.OK and n is None  # recovered, but no message


def test_renotify_cooldown():
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.0,
                                   renotify_cooldown_minutes=30))
    st = AlertState()
    n0 = ev.evaluate(st, 13.0, THR, 0)        # WARNING + notify at t=0
    assert n0 is not None
    n1 = ev.evaluate(st, 13.0, THR, 10 * MIN)  # still warning, < cooldown
    assert n1 is None
    n2 = ev.evaluate(st, 13.0, THR, 31 * MIN)  # past cooldown -> reminder
    assert n2 is not None and n2.kind == "reminder"


def test_renotify_disabled_when_zero():
    ev = AlertEvaluator(EvalConfig(debounce_count=1, renotify_cooldown_minutes=0))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)
    n = ev.evaluate(st, 13.0, THR, 100 * MIN)
    assert n is None


def test_one_sided_threshold():
    # Only a low alarm set (e.g. Helium volume must stay above 45.5).
    thr = Thresholds(warn_low=46.0, alarm_low=45.5)
    ev = AlertEvaluator(EvalConfig(debounce_count=1, hysteresis_frac=0.0))
    st = AlertState()
    assert ev.evaluate(st, 50.0, thr, 0) is None and st.level == AlertLevel.OK
    ev.evaluate(st, 45.8, thr, SEC)
    assert st.level == AlertLevel.WARNING
    ev.evaluate(st, 45.0, thr, 2 * SEC)
    assert st.level == AlertLevel.ALARM


def test_nodata_never_alerts_and_clears_pending():
    ev = AlertEvaluator(EvalConfig(debounce_count=2))
    st = AlertState()
    ev.evaluate(st, 13.0, THR, 0)        # pending warn (1)
    n = ev.evaluate(st, None, THR, SEC)  # NODATA
    assert n is None and st.pending_count == 0 and st.level == AlertLevel.OK


def test_inactive_thresholds_never_alert():
    thr = Thresholds()  # nothing set
    ev = AlertEvaluator(EvalConfig(debounce_count=1))
    st = AlertState()
    for v in (-1e9, 0, 1e9):
        assert ev.evaluate(st, v, thr, 0) is None
    assert st.level == AlertLevel.OK


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", f.__name__, "->", repr(e))
    print("---")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
