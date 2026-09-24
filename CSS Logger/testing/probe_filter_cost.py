"""How long does one shot-filter apply really take?

The shot filter recomputes a region's averages from the rows that matched, so
every keystroke in its "value" box could trigger a full _compute_stats over the
whole stack. On the worst real region measured so far (9007 shots x 2048 points)
that is a np.sort plus two percentiles over ~147 MB. This probe decides whether
the filter needs the lazy "compute only the stat keys the graph is showing" path
or whether a plain recompute is fast enough to keep the code simple.

No Qt, no archiver.

    python testing/probe_filter_cost.py
"""
import os
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                  # noqa: E402

import sp_t                                         # noqa: E402


def _timed(fn, *a, **kw):
    t0 = time.perf_counter()
    out = fn(*a, **kw)
    return (time.perf_counter() - t0) * 1000.0, out


def main():
    rng = np.random.default_rng(7)
    for n, nx in ((892, 2048), (3000, 2048), (9007, 2048), (9007, 4096)):
        stack = rng.random((n, nx), dtype=np.float64)
        mb = stack.nbytes / 1e6
        print(f"\n=== {n} shots x {nx} points  ({mb:.0f} MB) ===")

        arrs = list(stack)
        ms_all, _ = _timed(sp_t._compute_stats, arrs)
        print(f"  _compute_stats(all keys)          {ms_all:8.1f} ms")

        # What the filter really does: mask, copy the kept rows, recompute.
        mask = rng.random(n) < 0.1
        kept = int(mask.sum())
        ms_mask, sub = _timed(lambda: stack[mask])
        print(f"  stack[mask] -> {kept:5d} rows        {ms_mask:8.1f} ms")
        ms_sub, _ = _timed(sp_t._compute_stats, list(sub))
        print(f"  _compute_stats on the kept rows   {ms_sub:8.1f} ms")

        # The individual pieces, to see what a lazy path would save.
        for name, fn in (
            ("mean",    lambda: stack.mean(axis=0)),
            ("std",     lambda: stack.std(axis=0)),
            ("median",  lambda: np.median(stack, axis=0)),
            ("trimmed", lambda: sp_t._trimmed_mean(stack, 0.1)),
            ("sigma",   lambda: sp_t._sigma_clipped_mean(stack, 3.0)),
            ("p10+p90", lambda: (np.percentile(stack, 10, axis=0),
                                 np.percentile(stack, 90, axis=0))),
        ):
            ms, _ = _timed(fn)
            print(f"    {name:<10}                    {ms:8.1f} ms")

        worst = ms_mask + ms_all
        verdict = "LAZY KEYS NEEDED" if worst > 1500 else "plain recompute is fine"
        print(f"  worst case (filter keeps all)     {worst:8.1f} ms  -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
