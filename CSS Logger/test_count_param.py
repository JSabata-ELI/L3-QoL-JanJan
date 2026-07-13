"""Probe whether the CPVA endpoint honours the server-side `count` decimation
parameter (JSON archive-access protocol, Appendix B.3).

Run:  python "CSS Logger/test_count_param.py"

It requests one PV over ~30 days three ways and reports how many samples come
back and how long it takes:
  1) raw, single request (no count)      -> baseline
  2) count=2000                          -> should be ~2000 if decimation works
  3) count=500                           -> should be ~500 if decimation works

If (2)/(3) return roughly the requested counts AND are much faster than (1),
the server honours `count` and we can wire it in as the default fetch path.
If all three return the same huge number, the custom wrapper ignores `count`.
Also prints the `quality` of the first few points: "Interpolated" = decimated
(good), "Original" = raw.
"""
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from cpva_core import cpva_fetch_samples, dt_to_ns   # noqa: E402

PV = "HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy"   # change if you like

now = datetime.now(timezone.utc)
start_ns = dt_to_ns(now - timedelta(days=30))
end_ns   = dt_to_ns(now)


def _run(label, count):
    t0 = time.perf_counter()
    try:
        data = cpva_fetch_samples(PV, start_ns, end_ns, timeout=120.0, count=count)
    except Exception as exc:
        print(f"{label:<18} ERROR: {exc}")
        return
    dt = time.perf_counter() - t0
    quals = {}
    for s in data[:2000]:
        q = s.get("quality", "?")
        quals[q] = quals.get(q, 0) + 1
    print(f"{label:<18} {len(data):>8} samples  in {dt:6.1f}s   quality={quals}")


print(f"PV: {PV}")
print(f"window: {now - timedelta(days=30):%Y-%m-%d} -> {now:%Y-%m-%d}  (~30 days)\n")
_run("raw (no count)", None)
_run("count=2000", 2000)
_run("count=500", 500)
