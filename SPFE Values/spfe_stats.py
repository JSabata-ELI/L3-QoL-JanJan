"""
spfe_stats.py  --  "is this value far off compared with the other days?"

Statistical only. Nothing is hand-configured per quantity: the verdict comes
from the same slot on the previous working days.

The rule, and why each part of it is there:

* median + MAD, not mean + sigma. One bad day inflates sigma enough to hide the
  next one; the median does not move.
* a minimum number of history points, or the first weeks would flag everything.
* the flat case. A setpoint such as "Pumplaser DAC 24500" is identical every
  day, so MAD is 0 and *any* difference is infinitely many MADs -- a plain
  sigma rule turns a one-count change into an alarm. When the history is flat,
  a relative tolerance is used instead.

Nothing here deletes a value. A flagged value is still written; it is marked and
the reason is spelled out. A number nobody can see is worse than a number nobody
trusts.

No GUI toolkit, no network -- safe for headless tests.
"""
from __future__ import annotations

from dataclasses import dataclass

# 1.4826 * MAD estimates the standard deviation of a normal distribution.
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class Verdict:
    """The answer for one number in one cell."""
    flagged: bool
    reason: str = ""          # empty when there is nothing to say
    median: float | None = None
    spread: float | None = None       # the sigma-equivalent, or None when flat
    distance: float | None = None     # how many spreads away, or None when flat

    @property
    def has_history(self) -> bool:
        return self.median is not None


NO_HISTORY = Verdict(flagged=False, reason="not enough history yet")


def median(values: list[float]) -> float:
    """Plain median. Written out rather than imported so this module stays
    dependency-free and importable from a test with nothing installed."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def mad(values: list[float], centre: float | None = None) -> float:
    """Median absolute deviation."""
    if not values:
        return 0.0
    c = median(values) if centre is None else centre
    return median([abs(v - c) for v in values])


def judge(value: float,
          history: list[float],
          *,
          min_history: int = 8,
          mad_factor: float = 5.0,
          flat_tolerance_pct: float = 20.0,
          slot_label: str = "") -> Verdict:
    """Compare one value against the same slot on previous days.

    ``history`` is the recent values of that slot, newest or oldest first --
    order does not matter. ``value`` itself must not be in it.
    """
    clean = [float(v) for v in history if _is_number(v)]

    if len(clean) < min_history:
        return NO_HISTORY

    med = median(clean)
    spread = mad(clean, med) * _MAD_TO_SIGMA
    where = f" {slot_label}s" if slot_label else " records"
    seen = f"last {len(clean)}{where}"

    # ---- the flat case: every previous day read the same number -------------
    if spread <= 0.0:
        if med == 0.0:
            # A history of pure zeros. Any non-zero reading is a change worth
            # seeing; there is no scale to take a percentage of.
            if value == 0.0:
                return Verdict(False, "", med, None, None)
            return Verdict(
                True,
                f"the {seen} were all 0, this one is {_fmt(value)}",
                med, None, None,
            )
        off_pct = abs(value - med) / abs(med) * 100.0
        if off_pct > flat_tolerance_pct:
            return Verdict(
                True,
                f"{off_pct:.0f} % away from {_fmt(med)}, "
                f"which the {seen} all read",
                med, None, None,
            )
        return Verdict(False, "", med, None, None)

    # ---- the normal case ---------------------------------------------------
    distance = abs(value - med) / spread
    if distance > mad_factor:
        lo, hi = med - spread, med + spread
        return Verdict(
            True,
            f"{distance:.1f} x the usual spread -- {seen}: "
            f"median {_fmt(med)}, typical range {_fmt(lo)} to {_fmt(hi)}",
            med, spread, distance,
        )
    return Verdict(False, "", med, spread, distance)


def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v == v          # reject NaN
    return False


def _fmt(v: float) -> str:
    """Short readable number: no trailing .0, no scientific notation for the
    magnitudes this program deals with."""
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
