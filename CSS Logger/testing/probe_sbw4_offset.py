"""How late is the SBW4 energy archived relative to the PTM1 energy of the
same shot? And what does SBW4 read on PTM1's trigger shots?

The Conditions match a condition channel to a shot only if it is archived
within SAMPLE_HOLD_MIN_GAP_MS (137 ms) of the other channels; later than that
and the shot is judged on the previous shot's value. Reads raw archive data
for a few short windows (an hour each — never a whole day of a fast PV).

    python testing/probe_sbw4_offset.py [YYYY-MM-DD HH] ...
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np                     # noqa: E402

import cpva_core                       # noqa: E402

PTM1 = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"
SBW4_NAMES = (cpva_core.SBW4_HAPLS, cpva_core.SBW4_L3)
GAP_MS = cpva_core.SAMPLE_HOLD_MIN_GAP_MS


def _arr(samples):
    t, v = [], []
    for s in samples:
        val = s.get("value")
        if isinstance(val, list):          # the archiver wraps the value
            val = val[0] if val else None
        if isinstance(val, (int, float)):
            t.append(s["time"]); v.append(float(val))
    return np.array(t, dtype=np.int64), np.array(v, dtype=float)


def one_hour(start: datetime):
    a = int(start.timestamp() * 1e9)
    b = int((start + timedelta(hours=1)).timestamp() * 1e9)
    p_t, p_v = _arr(cpva_core.cpva_fetch_samples(PTM1, a, b, timeout=60))
    print(f"\n== {start:%Y-%m-%d %H:%M} +1 h: PTM1 {p_t.size} samples")
    if p_t.size < 5:
        return
    for name in SBW4_NAMES:
        s_t, s_v = _arr(cpva_core.cpva_fetch_samples(name, a, b, timeout=60))
        print(f"  {name}: {s_t.size} samples")
        if s_t.size < 5:
            continue
        # Nearest SBW4 sample to each PTM1 sample, signed (SBW4 - PTM1) in ms.
        j = np.clip(np.searchsorted(s_t, p_t), 1, s_t.size - 1)
        left, right = s_t[j - 1], s_t[j]
        near = np.where(np.abs(right - p_t) < np.abs(p_t - left), right, left)
        d_ms = (near - p_t) / 1e6
        q = np.percentile(d_ms, [1, 5, 25, 50, 75, 95, 99])
        print("    SBW4 - PTM1 [ms] percentiles 1/5/25/50/75/95/99:",
              " ".join(f"{x:.0f}" for x in q))
        print(f"    |offset| > {GAP_MS} ms on {np.mean(np.abs(d_ms) > GAP_MS) * 100:.1f} % "
              f"of PTM1 samples; > 0 (SBW4 later) on {np.mean(d_ms > 0) * 100:.1f} %")
        # Trigger shots: PTM1 below 5 J. What SBW4 does a hold-forward read
        # at +137 ms give them, and what does the SBW4 sample nearest say?
        trig = p_v < 5.0
        if trig.any():
            k = np.searchsorted(s_t, p_t[trig] + GAP_MS * 1_000_000, side="right") - 1
            held = np.where(k >= 0, s_v[np.maximum(k, 0)], np.nan)
            print(f"    PTM1 trigger shots: {int(trig.sum())}; SBW4 as the conditions "
                  f"read it >= 0.15 on {np.mean(held >= 0.15) * 100:.1f} % of them")


def main():
    if len(sys.argv) > 1:
        starts = [datetime.strptime(x, "%Y-%m-%d %H") for x in sys.argv[1:]]
    else:
        d = datetime.now().replace(minute=0, second=0, microsecond=0)
        starts = []
        while len(starts) < 3:
            d -= timedelta(hours=1)
            if d.weekday() < 5 and 9 <= d.hour <= 17:
                starts.append(d)
    for s in starts:
        try:
            one_hour(s)
        except Exception as exc:
            print(f"  {s}: {exc}")


if __name__ == "__main__":
    main()
