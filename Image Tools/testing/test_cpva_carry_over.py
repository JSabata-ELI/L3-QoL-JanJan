"""A carry-over value is not a day's data — and may even be stamped after it.

Measured on the real archiver 23.09.2026, asking `L3-SBW4-PM311:Energy` for
01.09.2026: ONE sample comes back, stamped **22.09.2026 21:24** — three weeks
after the window. The name holds nothing for that day; the HAPLS-era name holds
4 532 samples of it. Because the answer was not empty, the alias was never tried,
the Shot Finder clipped the single stray sample away and reported

    "no samples inside the chosen hours"

for every day of a three-week search but today. Two rules come out of it:

1. a sample stamped AFTER the window asked for is dropped — it is the archiver's
   carry-over, not a reading of those hours;
2. "this name has nothing here" means no sample INSIDE the window, which is what
   decides whether the measurement's other name is asked.

The sample from BEFORE the window stays: that one is the real held-forward value
a step channel needs.

No archiver, no Qt: the transport is a stub.

    python testing/test_cpva_carry_over.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import cpva_client as cpva                               # noqa: E402

FAILURES: "list[str]" = []
DAY = "2026-09-01"
NEW, OLD = cpva.SBW4_CHANNEL, cpva.SBW4_CHANNEL_LEGACY
T0, T1 = cpva.day_bounds_ns(DAY)
S = 1_000_000_000


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def reset():
    cpva._day_cache.clear()
    cpva._error_until.clear()


# What each name "holds", as the real archiver answered it: the L3 name replies
# with one value from three weeks later, the HAPLS name with the day itself.
STRAY = T1 + 21 * 24 * 3600 * S
STORE = {
    NEW: [(STRAY, 0.01846)],
    # Shots from 08:00 on, so a 07:00–21:00 read finds them too.
    OLD: ([(T0 - 2 * S, 19.4)]
          + [(T0 + 8 * 3600 * S + i * 60 * S, 20.0 + i * 0.01) for i in range(50)]),
}
ASKED: "list[str]" = []


def fake_values_ex(channel, start_ns, end_ns, *, timeout=0.0, try_value_suffix=True):
    ASKED.append(channel)
    # The stray value is returned WHATEVER the window, exactly as measured.
    if channel == NEW:
        return list(STORE[NEW]), channel
    # Like the real archiver: everything inside, plus the last value before it.
    inside = [(t, v) for t, v in STORE.get(channel, []) if start_ns <= t <= end_ns]
    before = [(t, v) for t, v in STORE.get(channel, []) if t < start_ns]
    return (before[-1:] + inside), channel


def main():
    orig = (cpva.fetch_values_ex, cpva.fetch_values)
    cpva.fetch_values_ex = fake_values_ex
    cpva.fetch_values = lambda ch, a, b, **kw: fake_values_ex(ch, a, b)[0]
    try:
        # ── 1. the stray sample does not count as data ────────────────────────
        reset()
        ASKED.clear()
        res = cpva.get_day(NEW, DAY)
        check("the day is answered by the name that actually holds it",
              res.src_channel == OLD, res.src_channel or "(none)")
        check("SBW4 is looked for under the HAPLS name first — one request",
              ASKED == [OLD], str(ASKED))
        check("no sample from after the window survives",
              all(t <= T1 for t, _ in res.samples),
              f"max={max((t for t, _ in res.samples), default=0)}")
        check("and the day's own samples are what comes back",
              len([1 for t, _ in res.samples if T0 <= t <= T1]) == 50,
              f"{len(res.samples)} sample(s)")

        # ── 2. the value from BEFORE the window is kept ───────────────────────
        check("the carry-over from before the window is kept (hold forward)",
              res.samples and res.samples[0][0] == T0 - 2 * S,
              str(res.samples[0][0] if res.samples else None))

        # ── 3. a window INSIDE the day behaves the same ───────────────────────
        # This is the Shot Finder's path: 07:00–21:00, not the whole day.
        reset()
        ASKED.clear()
        span = (T0 + 7 * 3600 * S, T0 + 21 * 3600 * S)
        res = cpva.get_day(NEW, DAY, span_ns=span)
        check("a picked-hours read falls back to the other name as well",
              res.src_channel == OLD and res.status == "ok",
              f"{res.src_channel} / {res.status}")

        # ── 4. a channel with no other name keeps its carry-over ──────────────
        # A step PV archived only when it changes answers a quiet day with the
        # value from before it, and that is a real reading — never an error.
        lone = "L3-PFWP6-MTR03-1:RawPos"
        STORE[lone] = [(T0 - 3600 * S, 42.0)]
        reset()
        ASKED.clear()
        res = cpva.get_day(lone, DAY)
        check("a channel with no alias is asked once",
              ASKED == [lone], str(ASKED))
        check("and its held-forward value is served as data",
              res.status == "ok" and len(res.samples) == 1
              and res.samples[0][1] == 42.0,
              f"{res.status}, {res.samples}")

        # ── 5. a stray sample alone, with no alias, is still dropped ──────────
        STORE[lone] = [(STRAY, 42.0)]
        reset()
        res = cpva.get_day(lone, DAY)
        check("a value stamped after the window is never served as the day's",
              res.samples == [] and res.status == "empty",
              f"{res.status}, {res.samples}")
    finally:
        cpva.fetch_values_ex, cpva.fetch_values = orig
        reset()

    # ── 6. the windowed raw read (Image Finder's region search) ───────────────
    got: "list[str]" = []

    def fake_samples(channel, start_ns, end_ns, **kw):
        got.append(channel)
        rows = STORE.get(channel, [])
        inside = [r for r in rows if start_ns <= r[0] <= end_ns]
        if inside:
            return [{"time": t, "value": v} for t, v in inside]
        # Nothing here: the archiver still answers with one bracketing sample,
        # and that one may be stamped after the window.
        return [{"time": t, "value": v} for t, v in rows[-1:]]

    orig_s = cpva.fetch_samples
    cpva.fetch_samples = fake_samples
    try:
        # if_t pulls Qt in; only the helper is wanted, so it is read out of the
        # source instead of importing the whole tab. Top-level definitions there
        # are separated by two blank lines, which is where the slice ends.
        src = (HERE.parent / "if_t.py").read_text(encoding="utf-8")
        start = src.index("def _cpva_fetch_samples")
        end = src.index("\n\n\n", start)
        body = src[start:end]
        check("the helper source was read whole", "read_order" in body,
              f"{len(body)} chars")
        ns = {"cpva": cpva, "CPVA_HTTP_TIMEOUT": 10.0}
        exec(compile(body, "if_t.py", "exec"), ns)
        out = ns["_cpva_fetch_samples"](NEW, T0, T1)
        check("the region read asks the HAPLS name first too",
              got == [OLD] and len(out) == 50, f"{got}, {len(out)} sample(s)")

        # And the other way round: when the HAPLS name is the one with only a
        # stray value, the L3 name is asked and answers.
        STORE[OLD], STORE[NEW] = STORE[NEW], STORE[OLD]
        got.clear()
        out = ns["_cpva_fetch_samples"](NEW, T0, T1)
        check("and falls back to the L3 name when HAPLS holds nothing",
              got == [OLD, NEW] and len(out) == 50, f"{got}, {len(out)} sample(s)")
        STORE[OLD], STORE[NEW] = STORE[NEW], STORE[OLD]
    finally:
        cpva.fetch_samples = orig_s

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
