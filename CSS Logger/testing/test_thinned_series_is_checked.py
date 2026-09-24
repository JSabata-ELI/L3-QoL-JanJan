"""
The server-thinned ("Optimized") series is not taken on trust.

With "Avg to" above zero the Logger asks the archiver for decimated samples
instead of the archived ones. Measured against the real archiver on 2026-09-21,
over the period 10.-12.9.2026, that answer is wrong in three different ways and
every one of them is silent:

  * HAPLS-SPEC_CENT_PD1M1_LT7_DIAG1:SpectralCentroid holds NOT ONE archived
    sample in that period (the neighbouring ones are 11.8. and 19.9.), yet the
    archiver answered with 4321 identical interpolated points, which the Logger
    drew as a solid three-day line.
  * L3-PCM3Y-MTR03-73:RawPos answered with 630 identical points that stop 61.5 h
    before the period ends, hiding the thirteen positions the motor really went
    through at 10:30 on the 10th. Asked raw, the same request returns them.
  * L3-PFWP6-MTR03-1:RawPos — the waveplate, and the master signal of the
    ramping work — has 1049 distinct archived positions in that period and came
    back as 90 minute-MEANS, so the graph showed positions the motor never stood
    on and a top of 999096 instead of 1000000.

And a fourth, in the carry-forward: the archiver brackets a range on both
sides, so "the last value BEFORE the window" was picked from the sample AFTER
it — the 10.9. window was seeded with a reading from the 19.9.

What this pins:

  * a sparse signal is read exactly as archived, a dense one is still thinned,
  * the sparse verdict does not cost a request per four hours,
  * a thinned series that never changes, or that stops early, is thrown away
    and read again as archived,
  * a dense signal whose thinned series is genuinely flat is left alone,
  * a re-read too big to hold is refused and said out loud,
  * the carry-forward never returns a sample from after the window,
  * "Avg to" = 0 still reads raw and never probes.

Runs headless with the network faked — no archiver needed.

Run:   python testing/test_thinned_series_is_checked.py
  or:  pytest testing/test_thinned_series_is_checked.py
"""
import os
import pathlib
import sys
import tempfile
import threading

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_HERE = pathlib.Path(__file__).resolve().parent
_APP = _HERE.parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="csslogger_test_")

import cpva_core

HOUR = int(3600e9)
DAY = 24 * HOUR
FAILURES = []


def check(name, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


# ── a fake archiver with the real one's decimation behaviour ────────────────

class _Chan:
    """One channel: its archived samples, and the level the server thins with.

    ``level`` is what a ``count=`` request is answered with, exactly as the real
    archiver answers it — a minute grid of interpolated points, independent of
    the archived samples, and possibly stopping early or never changing.
    ``None`` means the server has no level and falls back to the raw samples.
    """

    def __init__(self, raw, level=None, level_ends=None, level_value=None):
        self.raw = raw                      # [(time_ns, value), ...]
        self.level = level                  # "flat" | "real" | None
        self.level_ends = level_ends        # ns at which the level stops
        self.level_value = level_value


class _Fake:
    def __init__(self, chans):
        self.chans = chans
        self.calls = []
        self._lock = threading.Lock()

    def install(self):
        cpva_core.cpva_fetch_samples = self
        return self

    def raw_calls(self, channel):
        return sum(1 for c in self.calls if c[0] == channel and c[3] is None)

    def all_calls(self, channel):
        return sum(1 for c in self.calls if c[0] == channel)

    def __call__(self, channel, start_ns, end_ns, timeout=10.0, count=None):
        with self._lock:
            self.calls.append((channel, int(start_ns), int(end_ns), count))
        ch = self.chans[channel]
        if count and ch.level:
            end = min(int(end_ns), ch.level_ends or int(end_ns))
            out = []
            t = int(start_ns)
            step = 60 * int(1e9)
            while t <= end:
                if ch.level == "flat":
                    v = ch.level_value
                    lo = hi = v
                else:
                    v = float((t // step) % 97)
                    lo, hi = v - 0.5, v + 0.5
                out.append({"time": t, "value": v, "minimum": lo, "maximum": hi,
                            "type": "minMaxDouble", "quality": "Interpolated",
                            "metaData": {"units": "mm"}})
                t += step
            return out
        # raw: everything in range, plus the bracketing sample on each side,
        # which is what the real archiver does and what bit the carry-forward.
        inside = [s for s in ch.raw if start_ns <= s[0] <= end_ns]
        before = [s for s in ch.raw if s[0] < start_ns]
        after = [s for s in ch.raw if s[0] > end_ns]
        picked = ([max(before)] if before else []) + inside + \
                 ([min(after)] if after else [])
        return [{"time": t, "value": v, "quality": "Original", "type": "double",
                 "metaData": {"units": "mm"}} for t, v in picked]


END = 1_790_000_000_000_000_000
START = END - 3 * DAY


def _dense_raw(n_per_hour=3600, hours=72):
    step = HOUR // n_per_hour
    return [(START + i * step, float(i % 500)) for i in range(n_per_hour * hours)]


def _fetch(fake, channels, count=2000, **kw):
    kw.setdefault("preflight", False)
    return cpva_core.cpva_fetch_many_adaptive(channels, START, END,
                                              count=count, **kw)


# ── tests ───────────────────────────────────────────────────────────────────

def test_a_sparse_signal_is_read_as_archived():
    """The waveplate case: real positions, not minute-means."""
    real = [(START + 10 * HOUR + i * int(3e9), 1000.0 + i) for i in range(400)]
    fake = _Fake({
        "MOTOR": _Chan(real, level="real"),
        "METER": _Chan(_dense_raw(), level="real"),
    }).install()
    res, err, rep = _fetch(fake, ["MOTOR", "METER"])
    got = [s["value"] for s in res["MOTOR"]]
    check("the sparse signal comes back with every archived position",
          len(set(got)) == 400, f"{len(set(got))} distinct of 400")
    check("every point of it is an archived sample, not an interpolation",
          all(s["quality"] == "Original" for s in res["MOTOR"]))
    check("the dense signal is still thinned",
          all(s["quality"] == "Interpolated" for s in res["METER"]),
          f"{len(res['METER'])} points")
    check("nothing was left unread", not err, str(list(err)))


def test_the_sparse_verdict_is_cheap():
    """Reading a sparse signal raw must not mean a request per four hours."""
    real = [(START + i * HOUR, float(i)) for i in range(72)]
    fake = _Fake({"MOTOR": _Chan(real, level="real")}).install()
    _res, _err, rep = _fetch(fake, ["MOTOR"])
    check("a sparse signal over three days costs a handful of requests",
          rep.requests <= 4, f"{rep.requests} requests")


def test_a_flat_thinned_series_is_thrown_away():
    """The SpectralCentroid case: a level with nothing behind it."""
    fake = _Fake({
        "GHOST": _Chan([(START - 30 * DAY, 881.7), (END + 7 * DAY, 880.5)],
                       level="flat", level_value=881.7),
        "METER": _Chan(_dense_raw(), level="real"),
    }).install()
    notes = []
    res, _err, rep = _fetch(fake, ["GHOST", "METER"], log_fn=notes.append)
    inside = [s for s in res["GHOST"] if START <= s["time"] <= END]
    check("a period with nothing archived comes back empty, not as a flat line",
          not inside, f"{len(inside)} points")
    check("and the Log says the signal was read as archived",
          any("GHOST" in n or "archived" in n for n in notes), str(notes))


def test_a_short_thinned_series_is_thrown_away():
    """The L3-PCM3Y case: the level stops days before the period does."""
    moves = [(START - 28 * DAY, 1950.0)] + \
            [(START + 10 * HOUR + i * int(5e9), 2000.0 + 50 * i) for i in range(13)]
    fake = _Fake({"MOTOR": _Chan(moves, level="flat", level_value=1950.0,
                                 level_ends=START + 10 * HOUR + int(60e9))}).install()
    res, _err, rep = _fetch(fake, ["MOTOR"])
    vals = {s["value"] for s in res["MOTOR"] if START <= s["time"] <= END}
    check("the positions the thinned series hid are back",
          len(vals) == 13, f"{len(vals)} distinct values: {sorted(vals)[:4]}")


def test_a_dense_signal_that_really_is_flat_is_left_alone():
    """A stuck fast meter is not re-read: its thinned series is the truth."""
    fake = _Fake({"STUCK": _Chan(_dense_raw(), level="flat",
                                 level_value=7.0)}).install()
    res, _err, rep = _fetch(fake, ["STUCK"])
    check("a densely written signal keeps its thinned series",
          all(s["quality"] == "Interpolated" for s in res["STUCK"]),
          f"{len(res['STUCK'])} points")
    check("and it was never re-read in full", not rep.rechecked,
          str(rep.rechecked))


def test_a_re_read_too_big_to_hold_is_refused():
    """The guard against turning a flat level into a million samples."""
    saved = cpva_core.VERIFY_MAX_SAMPLES
    cpva_core.VERIFY_MAX_SAMPLES = 50
    try:
        real = [(START + i * int(6e9), float(i % 3)) for i in range(4000)]
        # Sparse enough in the probe hour to be re-read, but big overall.
        fake = _Fake({"BIG": _Chan(real, level="flat", level_value=1.0,
                                   level_ends=START + HOUR)}).install()
        notes = []
        res, _err, rep = _fetch(fake, ["BIG"], log_fn=notes.append)
        check("a read above the cap is dropped, not loaded",
              len(res["BIG"]) < 4000, f"{len(res['BIG'])} samples")
        check("and the Log says so",
              any("thinned after all" in n for n in notes), str(notes))
    finally:
        cpva_core.VERIFY_MAX_SAMPLES = saved


def test_the_carry_forward_never_looks_forward():
    """The seed for the left edge may not come from after the window."""
    fake = _Fake({"GHOST": _Chan([(START - 30 * DAY, 881.7),
                                  (END + 7 * DAY, 880.5)])}).install()
    got = cpva_core.cpva_fetch_last_before("GHOST", START, 10.0)
    check("a value is still found, however old it is", got is not None)
    check("and it predates the window",
          got is not None and got["time"] < START,
          f"{got['time'] if got else None} vs {START}")
    check("it is the real last value, not the one from after the window",
          got is not None and got["value"] == 881.7,
          str(got["value"] if got else None))


def test_avg_to_zero_still_means_raw():
    """With thinning switched off nothing is probed and nothing is checked."""
    fake = _Fake({"A": _Chan(_dense_raw(n_per_hour=10), level="real")}).install()
    res, _err, rep = _fetch(fake, ["A"], count=None)
    check("raw mode returns archived samples",
          all(s["quality"] == "Original" for s in res["A"]), f"{len(res['A'])}")
    check("raw mode does not check anything", not rep.rechecked)


def main():
    for fn in (test_a_sparse_signal_is_read_as_archived,
               test_the_sparse_verdict_is_cheap,
               test_a_flat_thinned_series_is_thrown_away,
               test_a_short_thinned_series_is_thrown_away,
               test_a_dense_signal_that_really_is_flat_is_left_alone,
               test_a_re_read_too_big_to_hold_is_refused,
               test_the_carry_forward_never_looks_forward,
               test_avg_to_zero_still_means_raw):
        print(f"\n── {fn.__name__} ──")
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print("  " + f)
        return 1
    print("all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
