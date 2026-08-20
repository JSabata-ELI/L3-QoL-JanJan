"""Assert a day too large for one archiver response is still read in full.

The report: a Shot Finder search over a busy day returned an empty results table.

Cause: /samples answers HTTP 500 once the asked-for window holds more than roughly
110 000 samples (measured on L3-SBW4-PM311:Energy — 2026-08-06 with 108 078 samples is
served, 2026-08-05 with 116 223 and 2026-08-01 with 122 410 are not, and each HALF of a
failing day comes back fine). It is a response-size limit: not a timeout, not missing
data, and not something a client can predict, because the count is only known after the
answer arrives. cpva_client.get_day turned the 500 into status="error", sf_t dropped the
day, and a search over one such day showed nothing at all.

Covered here (cpva_client.fetch_samples_split):
  [1] an oversize window is halved until it fits, and get_day returns EVERY sample;
  [2] an error splitting cannot fix (4xx, bad JSON) is raised at once — no split storm;
  [3] a window already at the floor is not split, whatever the error;
  [4] CpvaBusyError (our own pool, archiver never reached) is never split.

Runs against a fake transport — no network, no share:

  python testing/test_day_split.py
"""
import importlib.util
import sys
from pathlib import Path

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def load_cpva():
    p = Path(__file__).resolve().parent.parent / "cpva_client.py"
    spec = importlib.util.spec_from_file_location("cpva_client", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cpva_client"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeArchiver:
    """One sample per `step_ns`, but refuses any window it would answer with more
    than `max_samples` — exactly how the real one behaves at ~110k."""

    def __init__(self, cpva, step_ns=1_000_000_000, max_samples=1000,
                 error="HTTP 500 for /samples"):
        self.cpva = cpva
        self.step_ns = step_ns
        self.max_samples = max_samples
        self.error = error
        self.calls: "list[tuple[int, int]]" = []

    def __call__(self, channel, start_ns, end_ns, *, timeout=None):
        self.calls.append((start_ns, end_ns))
        first = ((start_ns + self.step_ns - 1) // self.step_ns) * self.step_ns
        times = list(range(first, end_ns + 1, self.step_ns))
        if self.max_samples is not None and len(times) > self.max_samples:
            raise self.cpva.CpvaError(self.error)
        return [{"time": t, "value": float(t % 97)} for t in times]


def case_oversize_day_is_read_in_pieces(cpva):
    """[1] The day the archiver refuses whole still arrives complete."""
    print("\n[1] a day too large for one response")
    cpva.invalidate()
    day = "2026-08-01"
    start, end = cpva.day_bounds_ns(day)
    # 1 sample / 10 s over a 24 h day = 8640, served 2048 at a time: the whole day and
    # both halves are refused, the four quarters fit.
    fake = FakeArchiver(cpva, step_ns=10_000_000_000, max_samples=2048)
    cpva.fetch_samples = fake
    splits_before = cpva.STATS.get("range_splits", 0)

    res = cpva.get_day("TEST:Big", day, timeout=1.0)

    expected = len([t for t in range(start, end + 1, 10_000_000_000)
                    if t >= ((start + 9_999_999_999) // 10_000_000_000) * 10_000_000_000])
    check("the day loads instead of erroring", res.status == "ok", f"status={res.status!r}")
    check("every sample is there", len(res.samples) == expected,
          f"{len(res.samples)} of {expected}")
    ts = [t for t, _ in res.samples]
    check("still sorted", ts == sorted(ts))
    check("and no sample is served twice", len(set(ts)) == len(ts),
          f"{len(ts) - len(set(ts))} duplicates")
    check("the split is counted", cpva.STATS.get("range_splits", 0) > splits_before,
          f"range_splits={cpva.STATS.get('range_splits')}")


def case_real_error_is_not_split(cpva):
    """[2] Splitting a 4xx would just repeat one error eight times."""
    print("\n[2] an error that splitting cannot fix")
    cpva.invalidate()
    day = "2026-08-02"
    fake = FakeArchiver(cpva, max_samples=0, error="HTTP 404 for /samples")
    cpva.fetch_samples = fake
    res = cpva.get_day("TEST:Gone", day, timeout=1.0)
    check("it is reported as an error", res.status == "error", f"status={res.status!r}")
    check("and asked exactly once", len(fake.calls) == 1, f"{len(fake.calls)} requests")

    cpva.invalidate()
    fake_json = FakeArchiver(cpva, max_samples=0, error="bad JSON from archiver: x")
    cpva.fetch_samples = fake_json
    cpva.get_day("TEST:Garbage", "2026-08-03", timeout=1.0)
    check("a malformed answer is not split either", len(fake_json.calls) == 1,
          f"{len(fake_json.calls)} requests")


def case_floor_is_respected(cpva):
    """[3] A short window that fails has a real problem — halving it is pointless."""
    print("\n[3] a window already at the floor")
    cpva.invalidate()
    fake = FakeArchiver(cpva, max_samples=0)
    cpva.fetch_samples = fake
    start = 1_785_535_200_000_000_000
    ten_min = 600_000_000_000
    try:
        cpva.fetch_samples_split("TEST:Short", start, start + ten_min, timeout=1.0)
        raised = False
    except cpva.CpvaError:
        raised = True
    check("the error reaches the caller", raised)
    check("and it was asked once, not halved", len(fake.calls) == 1,
          f"{len(fake.calls)} requests")

    # The bound also has to stop an endlessly failing LARGE window.
    cpva.invalidate()
    fake_all = FakeArchiver(cpva, max_samples=0)
    cpva.fetch_samples = fake_all
    day_start, day_end = cpva.day_bounds_ns("2026-08-04")
    try:
        cpva.fetch_samples_split("TEST:Hopeless", day_start, day_end, timeout=1.0)
    except cpva.CpvaError:
        pass
    check("a hopeless day gives up at the depth bound",
          len(fake_all.calls) <= 15, f"{len(fake_all.calls)} requests")


def case_busy_pool_is_not_split(cpva):
    """[4] A full LOCAL pool says nothing about the window — and splitting doubles
    the demand on the very pool that is already full."""
    print("\n[4] our own connection pool was full")
    cpva.invalidate()
    calls = []

    def busy(channel, start_ns, end_ns, *, timeout=None):
        calls.append((start_ns, end_ns))
        raise cpva.CpvaBusyError("connection pool exhausted")

    cpva.fetch_samples = busy
    day_start, day_end = cpva.day_bounds_ns("2026-08-05")
    try:
        cpva.fetch_samples_split("TEST:Busy", day_start, day_end, timeout=1.0)
        raised = ""
    except cpva.CpvaBusyError:
        raised = "busy"
    except cpva.CpvaError:
        raised = "plain"
    check("CpvaBusyError comes back as itself", raised == "busy", f"got {raised!r}")
    check("and nothing was split", len(calls) == 1, f"{len(calls)} requests")


def main():
    cpva = load_cpva()
    real_fetch = cpva.fetch_samples
    try:
        case_oversize_day_is_read_in_pieces(cpva)
        case_real_error_is_not_split(cpva)
        case_floor_is_respected(cpva)
        case_busy_pool_is_not_split(cpva)
    finally:
        cpva.fetch_samples = real_fetch
        cpva.invalidate()

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
