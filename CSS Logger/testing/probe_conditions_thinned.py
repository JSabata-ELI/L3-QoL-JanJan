"""Probe: how the Conditions judge a long (thinned) window — real archiver.

Reads the condition channels over a past window at the Logger's default
detail (count=2000, cache off) and counts, per condition and for all of them
together, the thinned points that pass when judged by their MEAN (what the
Logger did) versus by their min/max RANGE overlapping the condition. No window,
no writes to the share.

    python probe_conditions_thinned.py [YYYY-MM-DD] [days]
"""
import os
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import cpva_core as cpva  # noqa: E402

CONDS = [  # the conditions in cpva_explorer_config.json (12.10.2026)
    ("HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy", 5.0, 150.0),
    ("HAPLS-ENER_IN_PCM4_LT5_DIAG2:Energy", 0.2, 100.0),
    ("HAPLS-ENER_IN_PAP1_LT7_DIAG2:Energy", 0.021, 1.0),
]


def _num(v):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
    return v if isinstance(v, (int, float)) else None


def main():
    start = datetime.strptime(sys.argv[1], "%Y-%m-%d") if len(sys.argv) > 1 \
        else datetime(2025, 9, 1)
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    t0, t1 = start, start + timedelta(days=days)
    s_ns, e_ns = int(t0.timestamp() * 1e9), int(t1.timestamp() * 1e9)
    chans = [c[0] for c in CONDS]
    tic = time.time()
    res, errs, rep = cpva.cpva_fetch_many_adaptive(
        chans, s_ns, e_ns, count=2000, cache=None,
        log_fn=lambda m: print("  log:", m))
    print(f"window {t0:%Y-%m-%d} + {days} d, read {time.time() - tic:.1f} s, "
          f"errors {errs or '-'}")
    per = {}
    for ch, lo_c, hi_c in CONDS:
        s = res.get(ch) or []
        thinned = sum(1 for x in s if x.get("minimum") is not None)
        mean_ok = rng_ok = 0
        by_t = {}
        for x in s:
            v = _num(x.get("value"))
            if v is None:
                continue
            lo = _num(x.get("minimum"))
            hi = _num(x.get("maximum"))
            lo = v if lo is None else lo
            hi = v if hi is None else hi
            m = lo_c <= v <= hi_c
            r = hi >= lo_c and lo <= hi_c
            mean_ok += m
            rng_ok += r
            day = datetime.fromtimestamp(x["time"] / 1e9).strftime("%m-%d")
            d = by_t.setdefault(day, [0, 0, 0])
            d[0] += 1
            d[1] += m
            d[2] += r
        per[ch] = by_t
        print(f"{cpva.shorten_pv_name(ch):>12}: {len(s)} pts ({thinned} thinned), "
              f"pass by mean {mean_ok}, by range {rng_ok}")
    print("day    " + "  ".join(f"{cpva.shorten_pv_name(c[0]):>16}" for c in CONDS)
          + "   (points / pass-mean / pass-range)")
    all_days = sorted(set().union(*[set(v) for v in per.values()]))
    for day in all_days:
        cells = []
        for ch, *_ in CONDS:
            n, m, r = per[ch].get(day, (0, 0, 0))
            cells.append(f"{n:>5}/{m:>4}/{r:>4}")
        print(f"{day}  " + "  ".join(f"{c:>16}" for c in cells))


if __name__ == "__main__":
    main()
