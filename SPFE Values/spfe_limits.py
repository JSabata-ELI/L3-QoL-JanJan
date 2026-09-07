"""
spfe_limits.py  --  "is this value inside the range somebody said is good?"

The other judge in this program, spfe_stats, asks a different question: "is
this number unlike the other days?". That one needs no setting up and knows
nothing about what is actually acceptable. This one is the opposite: a person
types the range a quantity is allowed to be in, and every value is measured
against it -- so a quantity that has been slowly drifting out of spec for a
fortnight is caught, which the statistical judge never can, because by then the
drift IS the history.

The two live side by side and never overwrite each other's verdict: a reference
is somebody's decision, so it is the one that colours the cell, and the
statistical remark is kept in the same tooltip underneath it.

Three levels, and the middle one is the point of the whole feature:

    ""      inside the range, comfortably
    "warn"  still inside, but within `warn_pct` of an edge -- the value is on
            its way out and there is still time to do something
    "bad"   outside the range

Nothing here is a GUI or a file: one function judges one number. Safe to import
from a test with nothing installed.
"""
from __future__ import annotations

from dataclasses import dataclass

# What "close to the edge" means when nobody says otherwise. A percentage, not
# an absolute step, because these quantities range from 0.1 to 24 000.
DEFAULT_WARN_PCT = 5.0

LEVEL_OK   = ""
LEVEL_WARN = "warn"
LEVEL_BAD  = "bad"

# Which of two levels is the more serious. Used when one cell holds several
# numbers -- the workbook writes X;Y;SUM into a single cell, and the colour of
# that cell has to be the colour of its worst number.
_RANK = {LEVEL_OK: 0, LEVEL_WARN: 1, LEVEL_BAD: 2}


@dataclass(frozen=True)
class LimitVerdict:
    """The answer for one number against one reference."""
    level: str = LEVEL_OK
    reason: str = ""
    low: float | None = None
    high: float | None = None

    @property
    def flagged(self) -> bool:
        return self.level != LEVEL_OK


OK = LimitVerdict()


def worse(a: str, b: str) -> str:
    """The more serious of two levels."""
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


def as_number(v) -> float | None:
    """A reference bound as a number, or None for "not set".

    Anything unusable -- an empty box, a word, a blank -- is "not set" rather
    than an error: half a reference (only a minimum, or only a maximum) is a
    perfectly ordinary thing to want, and a typo must not take the colouring
    off every other quantity with it.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if v != v else float(v)          # reject NaN
    text = str(v).strip().replace(",", ".")
    if not text:
        return None
    try:
        f = float(text)
    except ValueError:
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def normalise(entry: dict) -> dict:
    """One reference as this module wants it: {"min", "max", "warn_pct"}.

    A minimum above the maximum is swapped rather than refused. It is what
    happens when somebody fills the two boxes in the order they think of them,
    and the range they meant is obvious.
    """
    lo = as_number(entry.get("min"))
    hi = as_number(entry.get("max"))
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    pct = as_number(entry.get("warn_pct"))
    if pct is None or pct < 0:
        pct = None
    return {"min": lo, "max": hi, "warn_pct": pct}


def has_range(entry: dict | None) -> bool:
    """Whether this reference says anything at all."""
    if not entry:
        return False
    n = normalise(entry)
    return n["min"] is not None or n["max"] is not None


def _band(lo: float | None, hi: float | None, pct: float,
          value: float) -> float:
    """How far from an edge still counts as "close to it".

    `pct` of the width of the range, which is the only measure that makes sense
    when both edges are given: 5 % of a 100-to-200 range is 5, at both ends.

    With one edge only there is no width, so it is `pct` of the edge itself --
    5 % below a minimum of 24 500 is 1 225. An edge of exactly zero has no
    scale either, and there the value itself is the only thing left to take a
    percentage of.
    """
    if pct <= 0:
        return 0.0
    if lo is not None and hi is not None and hi > lo:
        return (hi - lo) * pct / 100.0
    edge = lo if lo is not None else hi
    scale = abs(edge) if edge else abs(value)
    return scale * pct / 100.0


def judge(value, entry: dict | None, *,
          default_warn_pct: float = DEFAULT_WARN_PCT) -> LimitVerdict:
    """One number against one reference.

    `entry` is what the References window stored for this number:
    ``{"min": .., "max": .., "warn_pct": ..}``, any of them missing. A
    reference with neither edge gives no verdict at all -- that is how a
    quantity is left uncoloured, and it is the state every quantity starts in.
    """
    v = as_number(value)
    if v is None:
        return OK
    n = normalise(entry or {})
    lo, hi = n["min"], n["max"]
    if lo is None and hi is None:
        return OK
    pct = n["warn_pct"]
    if pct is None:
        pct = float(default_warn_pct)

    if lo is not None and v < lo:
        return LimitVerdict(LEVEL_BAD,
                            f"below the reference: {_fmt(v)} < {_fmt(lo)}"
                            f"{_range_tail(lo, hi)}", lo, hi)
    if hi is not None and v > hi:
        return LimitVerdict(LEVEL_BAD,
                            f"above the reference: {_fmt(v)} > {_fmt(hi)}"
                            f"{_range_tail(lo, hi)}", lo, hi)

    band = _band(lo, hi, pct, v)
    if band > 0:
        if lo is not None and (v - lo) <= band:
            return LimitVerdict(
                LEVEL_WARN,
                f"close to the low edge: {_fmt(v)}, and the reference stops "
                f"at {_fmt(lo)}{_range_tail(lo, hi)}", lo, hi)
        if hi is not None and (hi - v) <= band:
            return LimitVerdict(
                LEVEL_WARN,
                f"close to the high edge: {_fmt(v)}, and the reference stops "
                f"at {_fmt(hi)}{_range_tail(lo, hi)}", lo, hi)
    return LimitVerdict(LEVEL_OK, "", lo, hi)


def describe(entry: dict | None, *,
             default_warn_pct: float = DEFAULT_WARN_PCT) -> str:
    """A reference in one short line, for a tooltip and for the window."""
    if not has_range(entry):
        return ""
    n = normalise(entry or {})
    lo, hi = n["min"], n["max"]
    pct = n["warn_pct"]
    if pct is None:
        pct = float(default_warn_pct)
    if lo is not None and hi is not None:
        span = f"{_fmt(lo)} to {_fmt(hi)}"
    elif lo is not None:
        span = f"at least {_fmt(lo)}"
    else:
        span = f"at most {_fmt(hi)}"
    return f"reference {span}, amber within {_fmt(pct)} %"


def _range_tail(lo: float | None, hi: float | None) -> str:
    """" (reference 100 to 200)", or nothing when there is only one edge -- it
    has already been named in the sentence itself."""
    if lo is None or hi is None:
        return ""
    return f" (reference {_fmt(lo)} to {_fmt(hi)})"


def _fmt(v: float) -> str:
    """Short readable number, the same shape spfe_stats prints."""
    if v != v:
        return "nan"
    a = abs(v)
    if a >= 1e7 or (a < 1e-3 and a != 0):
        return f"{v:.3e}"
    if a >= 100:
        return f"{v:,.0f}".replace(",", " ")
    if a >= 1:
        return f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{v:.4f}".rstrip("0").rstrip(".")
