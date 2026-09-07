"""The hall verdict and the widening look-back, without opening a window.

Run:  python test_hall_logic.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import a  # noqa: E402

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}\n    got  {got!r}\n    want {want!r}")
        print(f"FAIL  {name}")
    else:
        print(f"ok    {name}")


def verdict(cond, fate, pss, moving=None):
    return a._hall_verdict(cond, fate, pss, moving)[0]


def hall(hall_value=None, pss_value=None, grace=60):
    return {"hall": hall_value, "pss": pss_value, "moving_grace_s": grace}


# --- nothing set -----------------------------------------------------------
check("neither half set is not a judgement",
      verdict(hall(), 3, 1), "unknown")

# --- the PSS half alone ----------------------------------------------------
check("PSS matches", verdict(hall(pss_value=1), None, 1), "ok")
check("PSS differs", verdict(hall(pss_value=1), None, 0), "trip")
check("PSS unreadable never trips", verdict(hall(pss_value=1), None, None), "unknown")
check("PSS 0 expected and 0 read is fine, not 'unset'",
      verdict(hall(pss_value=0), None, 0), "ok")

# --- the hall half alone ---------------------------------------------------
check("beam where it was set", verdict(hall(hall_value=3), 3, None), "ok")
check("beam somewhere else", verdict(hall(hall_value=3), 2, None), "trip")
check("beam unreadable never trips", verdict(hall(hall_value=3), None, None), "unknown")

# --- the switchyard on the move -------------------------------------------
check("moving, inside the grace", verdict(hall(hall_value=3, grace=60), 0, None, 10), "warn")
check("moving, just inside", verdict(hall(hall_value=3, grace=60), 0, None, 60), "warn")
check("moving, past the grace", verdict(hall(hall_value=3, grace=60), 0, None, 61), "trip")
check("moving, how long is unknown", verdict(hall(hall_value=3, grace=60), 0, None, None), "warn")
check("moving with a zero grace trips at once",
      verdict(hall(hall_value=3, grace=0), 0, None, 0.5), "trip")

# --- both halves -----------------------------------------------------------
check("both match", verdict(hall(3, 1), 3, 1), "ok")
check("hall right, PSS wrong", verdict(hall(3, 1), 3, 0), "trip")
check("hall wrong, PSS right", verdict(hall(3, 1), 2, 1), "trip")
check("one of the two unreadable", verdict(hall(3, 1), None, 1), "unknown")

# --- what the sentence says ------------------------------------------------
_, said = a._hall_verdict(hall(3, None), 2, None, None)
check("the sentence names both halls", ("E3" in said and "E4" in said), True)
_, said = a._hall_verdict(hall(None, 1), None, 0, None)
check("the PSS sentence names both states",
      ("internal only" in said and "into the experiment" in said), True)
_, said = a._hall_verdict(hall(3, 1), None, None, None)
check("an unreadable pair names both PVs",
      ("beam fate" in said and "PSS state" in said), True)

# --- floats out of the settings file behave like the ints in the table -----
check("a hall stored as 3.0 still matches 3", verdict(hall(3.0, None), 3.0, None), "ok")
check("_hall_name works on a float", a._hall_name(4.0), "E5 ELI-LUIS")
check("an unknown number is not silently a hall", a._hall_name(9.0), "unknown value 9")
check("no reading is said out loud", a._hall_name(None), "not readable")

# --- the widening look-back ------------------------------------------------
class _FakeTracker:
    """Only what _fetch_latest touches."""
    def __init__(self, answer_from_span_s):
        self.answer_from = answer_from_span_s
        self.asked = []
        self._hall_span_hint = {}

    def _fetch_samples(self, pv, start_ns, end_ns, context=None):
        span = round((end_ns - start_ns) / 1e9)
        self.asked.append(span)
        return [(end_ns - 5, 1.0)] if span >= self.answer_from else []

    _fetch_latest = a.ScreenTracker._fetch_latest


now = 1_000_000_000_000_000_000
WIDEST = a.HALL_LOOKBACK_S[-1]

t = _FakeTracker(answer_from_span_s=a.HALL_LOOKBACK_S[0])
check("an hour is enough, nothing wider is asked",
      (t._fetch_latest("X", now) is not None, t.asked), (True, [3600]))

t = _FakeTracker(answer_from_span_s=a.HALL_LOOKBACK_S[2])
res = t._fetch_latest("X", now)
check("it widens until something comes back", t.asked, list(a.HALL_LOOKBACK_S[:3]))
check("and returns a (value, time) pair", (res[0], isinstance(res[1], int)), (1.0, True))

t = _FakeTracker(answer_from_span_s=10 ** 9)
check("a channel with nothing at all is None, after every window",
      (t._fetch_latest("X", now), t.asked), (None, list(a.HALL_LOOKBACK_S)))

# The window that worked is remembered, so the next read costs one request.
t = _FakeTracker(answer_from_span_s=a.HALL_LOOKBACK_S[2])
t._fetch_latest("X", now)
t.asked.clear()
t._fetch_latest("X", now)
check("the window that worked is tried first next time",
      t.asked, [a.HALL_LOOKBACK_S[2]])

# A dead channel must not cost four requests on every read: once the widest
# window came back empty, only the widest is asked again — a narrower window is
# a subset of it and could not hold anything the wide one missed.
t = _FakeTracker(answer_from_span_s=10 ** 9)
t._fetch_latest("X", now)
t.asked.clear()
t._fetch_latest("X", now)
check("a dead channel costs one request from then on", t.asked, [WIDEST])

# And it heals: the same channel starts answering, from the widest window.
t.answer_from = WIDEST
t.asked.clear()
check("a channel that comes back is read again",
      (t._fetch_latest("X", now)[0], t.asked), (1.0, [WIDEST]))

# Each channel keeps its own hint.
t = _FakeTracker(answer_from_span_s=a.HALL_LOOKBACK_S[1])
t._fetch_latest("X", now)
t.asked.clear()
t._fetch_latest("Y", now)
check("a second channel starts from the narrowest window on its own",
      t.asked, list(a.HALL_LOOKBACK_S[:2]))

print()
if FAILED:
    print(f"{len(FAILED)} FAILED:")
    for f in FAILED:
        print("  " + f)
    sys.exit(1)
print("all passed")
