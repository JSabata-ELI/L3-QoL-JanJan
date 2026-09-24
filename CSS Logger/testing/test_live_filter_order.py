"""In live mode the shot filter runs BEFORE "average last N".

This is the bug of 2026-09-23 nailed down. The user had 200 in "Average last N"
and a condition GDD = 24700. GDD stood at 24700 all morning and was stepped to
25100 at noon, so the last 200 shots carried nothing but 25100 — and the panel
said "nothing matches" although the morning was in the buffer. N ran first, the
filter second.

What has to hold now:
  * the filter sweeps the WHOLE buffer, and N then takes the last N of what
    matched — so a condition on a morning value returns the morning shots;
  * the counters keep three separate numbers: raw shots in the window, how many
    matched, how many the graph uses;
  * "newer filtered out" still counts RAW shots after the last match, so the red
    curve's label does not change meaning;
  * with no condition, nothing changes — the last N raw shots, as before.

No archive is read: the buffer and the filter PV series are made up here.

    python testing/test_live_filter_order.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from collections import deque                       # noqa: E402

import numpy as np                                   # noqa: E402
from PySide6.QtWidgets import QApplication           # noqa: E402

import sp_t                                          # noqa: E402

FAILED = []
GDD = "L3-SPFE-AOD03-002:Order2_RB"
SEC = int(1e9)


def check(name, ok, detail=""):
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def _load(w, n_total: int, n_matching: int, target=24700.0, other=25100.0):
    """A buffer of n_total shots, one every 10 s, where only the OLDEST
    n_matching were taken at `target` — the shape of the real morning."""
    t0 = 1_700_000_000 * SEC
    w._live = True
    w._live_buf = deque(((t0 + i * 10 * SEC, np.full(8, float(i)))
                         for i in range(n_total)), maxlen=sp_t.LIVE_BUF_MAX)
    # Two set-point writes: target before the first shot, other in the middle.
    w._live_scalars = {GDD: [(t0 - 60 * SEC, target),
                             (t0 + n_matching * 10 * SEC, other)]}


def _condition(w, value):
    w._filter_on = True
    w._filter_conds = [{"label": "GDD", "channel": GDD, "value": value,
                        "tol": 0.0, "on": True}]


def main():
    app = QApplication.instance() or QApplication([])   # noqa: F841
    w = sp_t.SpectraWidget()

    # ── The reported case: 300 shots, only the oldest 50 match, N = 200 ──
    _load(w, 300, 50)
    _condition(w, 24700.0)
    got = w._live_shots(200)
    check("the morning shots are found, not an empty list", len(got) == 50,
          f"{len(got)} shots")
    check("they are the oldest 50",
          bool(got) and got[0][0] == list(w._live_buf)[0][0]
          and got[-1][0] == list(w._live_buf)[49][0])
    check("raw shots in the window", w._live_seen_n == 300, str(w._live_seen_n))
    check("matched", w._live_matched_n == 50, str(w._live_matched_n))
    check("used by the graph", w._live_kept_n == 50, str(w._live_kept_n))
    check("newer raw shots after the last match",
          w._live_newer_dropped == 250, str(w._live_newer_dropped))

    # ── N really does trim the matching ones ─────────────────────────────
    got = w._live_shots(20)
    check("N takes the LAST 20 of the matching ones", len(got) == 20,
          f"{len(got)} shots")
    check("and they are the newest matching ones",
          bool(got) and got[-1][0] == list(w._live_buf)[49][0])
    check("matched is unchanged by N", w._live_matched_n == 50,
          str(w._live_matched_n))
    check("used follows N", w._live_kept_n == 20, str(w._live_kept_n))

    # ── The other setting is reachable the same way ──────────────────────
    _condition(w, 25100.0)
    got = w._live_shots(200)
    check("the noon setting returns its own 250 shots", len(got) == 200,
          f"{len(got)} of {w._live_matched_n} matching")
    check("nothing newer was dropped there", w._live_newer_dropped == 0,
          str(w._live_newer_dropped))

    # ── A value nobody was ever at ───────────────────────────────────────
    _condition(w, 12345.0)
    got = w._live_shots(200)
    check("a value never set returns nothing", got == [], str(len(got)))
    check("and says so — 0 of 300", (w._live_matched_n, w._live_seen_n) == (0, 300),
          f"{w._live_matched_n} of {w._live_seen_n}")

    # ── No condition: the old behaviour, untouched ───────────────────────
    w._filter_on = False
    got = w._live_shots(200)
    check("no condition = the last 200 raw shots", len(got) == 200, str(len(got)))
    check("and they are the newest ones",
          got[-1][0] == list(w._live_buf)[-1][0])
    check("matched equals seen with no condition",
          w._live_matched_n == w._live_seen_n == 300)

    # ── N larger than the buffer ─────────────────────────────────────────
    _condition(w, 24700.0)
    got = w._live_shots(5000)
    check("N past the buffer takes all the matching ones", len(got) == 50,
          str(len(got)))

    # ── A filter PV that has not reported yet cannot reject anything ─────
    w._live_scalars = {}
    got = w._live_shots(200)
    check("an unarmed channel keeps the shots", len(got) == 200, str(len(got)))
    check("and the panel knows it is arming", w._live_arming == [GDD],
          str(w._live_arming))

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
