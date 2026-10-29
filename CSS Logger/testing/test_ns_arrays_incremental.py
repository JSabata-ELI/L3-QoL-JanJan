"""_samples_to_ns_arrays reads only the new end of a Live list.

Live appends to the same list and trims its start on every tick; converting
the whole list each time was a large share of a refresh. The shortcut must
give exactly what a full conversion gives — after appends, trims, both,
a sample put in the middle (a late arrival) and a word value.

    python testing/test_ns_arrays_incremental.py
"""
import bisect
import os
import random
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                              # noqa: E402
import main as css                              # noqa: E402

FAILURES: "list[str]" = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def _full(samples):
    pairs = [(t, v) for t, v, _ in samples if isinstance(v, (int, float))]
    return (np.array([p[0] for p in pairs], dtype=float),
            np.array([p[1] for p in pairs], dtype=float))


def _same(samples):
    ts, v = css._samples_to_ns_arrays(samples)
    ft, fv = _full(samples)
    return ts.shape == ft.shape and np.array_equal(ts, ft) and np.array_equal(v, fv)


def main() -> int:
    print("test_ns_arrays_incremental")
    rnd = random.Random(7)
    s = [(i * 1000, float(i), "J") for i in range(500)]
    check("first conversion", _same(s))
    t = 500 * 1000
    ok = True
    for step in range(200):
        for _ in range(rnd.randint(0, 5)):          # new samples at the end
            t += 1000
            s.append((t, rnd.random(), "J"))
        if rnd.random() < 0.5:                      # trim the start
            del s[:rnd.randint(0, 8)]
        if rnd.random() < 0.1 and len(s) > 10:      # a late sample in the middle
            mid = (s[len(s) // 2][0] + s[len(s) // 2 + 1][0]) // 2
            bisect.insort(s, (mid, -1.0, "J"), key=lambda x: x[0])
        if not _same(s):
            ok = False
            break
    check("200 Live-like ticks match a full conversion", ok, f"step {step}")
    s.append((t + 5000, "OPEN", ""))                # a word value
    check("a word value drops out as in a full conversion", _same(s))
    s.append((t + 6000, 3.0, "J"))
    check("…and numbers after it still match", _same(s))
    print("FAILED: " + "; ".join(FAILURES) if FAILURES else "ALL PASS")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
