"""Does a PV that is written only when it CHANGES read its real value?

The Image Slider showed "no data yet" for the GDD setting L3-SPFE-AOD03-002:Order2_RB
(2026-09-01): the value had stood at 24300 since 10:35 that morning, so no ±0.3 s
window around an image ever touched a sample. cpva_client now classifies a channel
from its own record and answers a settings channel with its last written value, while
a per-shot detector still says "n/a" rather than hand out a neighbouring shot's number.

Two halves:
  * offline — the classifier on synthetic sample runs, including the two cases that
    broke the obvious statistics (an overnight gap, a burst of nudges);
  * live — the real archiver, if it is reachable from this machine.

Run:  python "Image Tools/testing/test_step_channel_detect.py"
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cpva_client as cpva      # noqa: E402

SEC = 1_000_000_000
fails: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {what}{('  — ' + detail) if detail else ''}")
    if not ok:
        fails.append(what)


def run(ts_list: "list[int]") -> "bool | None":
    return cpva._holds_between_samples(sorted(ts_list))


def gap(ts_list: "list[int]") -> str:
    p75 = cpva._typical_gap_s(sorted(ts_list))
    held = cpva.hold_fraction(sorted(ts_list))
    if p75 is None:
        return "-"
    return f"typical gap {p75:.1f} s, holding {held * 100:.1f} % of the time"


def offline() -> None:
    print("[1] the verdict on synthetic sample runs")
    now = int(time.time()) * SEC

    # A per-shot detector: one sample every 5 s.
    shots = [now - i * 5 * SEC for i in range(20)]
    check(run(shots) is False, "energy detector reads as a live channel", gap(shots))

    # The same detector asked about at 07:00: today has barely started, so the run
    # reaches back over the night into yesterday's shift. That one overnight gap is
    # most of the elapsed time but nowhere near all of it.
    morning = ([now - 13 * 3600 * SEC - i * 60 * SEC for i in range(600)]
               + [now, now - 5 * SEC])
    check(run(morning) is False,
          "one overnight gap does not make a detector a setting", gap(morning))

    # A real slow run: one shot every 25 s, dead even. This is the cadence the panel's
    # "trailing by a shot" bug was reported at — it must NOT be held forward.
    slow_shots = [now - i * 25 * SEC for i in range(12)]
    check(run(slow_shots) is False,
          "a shot every 25 s is still a measurement, not a setting", gap(slow_shots))

    # A setting nudged five times in five seconds, twice a day otherwise. The MEDIAN
    # gap here is 2 s — the verdict must not follow that either.
    nudges = [now - 3 * 3600 * SEC + i * SEC for i in range(6)]
    setting = [now - 4 * 24 * 3600 * SEC, now - 26 * 3600 * SEC,
               now - 5 * 3600 * SEC] + nudges
    check(run(setting) is True,
          "a burst of nudges does not make a setting a detector", gap(setting))

    # Two samples a week apart — the plainest settings channel there is.
    rare = [now - 7 * 24 * 3600 * SEC, now]
    check(run(rare) is True, "two samples a week apart read as a setting", gap(rare))

    check(run([now]) is None, "a single sample gives no verdict")


def offline_classifier() -> None:
    """The classifier itself, on a made-up archive: the cases where the answer is
    not in the last few samples and the walk back through the days has to find it."""
    print("[2] the walk back through the days")
    now = int(time.time()) * SEC
    old = now - 30 * 24 * 3600 * SEC        # its last sample, a month back

    real_get_day = cpva.get_day
    real_before = cpva.value_at_or_before

    def fake_archive(day_samples: "dict[str, list[tuple[int, float]]]"):
        def get_day(channel, date_key, **kw):
            return cpva.DayResult(day_samples.get(date_key, []), "ok", 0.0,
                                  [t for t, _v in day_samples.get(date_key, [])])

        def value_at_or_before(channel, ts_ns, **kw):
            best = None
            for rows in day_samples.values():
                for t, v in rows:
                    if t <= ts_ns and (best is None or t > best[0]):
                        best = (t, v)
            if best is None:
                return cpva.LookupResult(None, None, "not_found")
            return cpva.LookupResult(best[1], best[0], "ok")

        cpva.get_day = get_day
        cpva.value_at_or_before = value_at_or_before

    try:
        old_dk = cpva.date_key_for_ns(old)
        # A setting: a couple of changes that day, then a month of silence.
        cpva.invalidate_step_verdicts()
        fake_archive({old_dk: [(old - 4 * 3600 * SEC, 1.0), (old - SEC, 2.0),
                               (old, 2.5)]})
        check(cpva.classify_step_channel("FAKE:Setting", now),
              "a setting untouched for a month is still held forward")

        # A detector that stopped a month ago: its own day is dense.
        cpva.invalidate_step_verdicts()
        fake_archive({old_dk: [(old - i * 5 * SEC, float(i)) for i in range(200)]})
        check(not cpva.classify_step_channel("FAKE:DeadDetector", now),
              "a detector that stopped a month ago is NOT held forward")

        # A setting somebody is working on RIGHT NOW: eighteen writes in ten minutes.
        # Today on its own looks like a live channel; the quiet days behind it are what
        # say otherwise, and the walk has to reach them.
        cpva.invalidate_step_verdicts()
        today_dk = cpva.date_key_for_ns(now)
        back_dk = cpva.date_key_for_ns(now - 3 * 24 * 3600 * SEC)
        burst = [(now - 600 * SEC + i * SEC, 1.0) for i in range(6)]
        burst += [(now - 400 * SEC + i * SEC, 2.0) for i in range(6)]
        burst += [(now - 90 * SEC + i * SEC, 3.0) for i in range(6)]
        fake_archive({today_dk: burst,
                      back_dk: [(now - 3 * 24 * 3600 * SEC, 0.5)]})
        # Ten minutes of one-second writes look exactly like a live channel if that
        # is all you look at. The quiet days behind them are what say otherwise, and
        # the walk has to reach far enough back to see them.
        check(cpva.classify_step_channel("FAKE:Tweaked", now),
              "a setting being tweaked right now still reads as a setting")
        check(cpva._cached_step_verdict("FAKE:Tweaked") is not None,
              "and the verdict is remembered — but not for longer than the TTL",
              f"{cpva.STEP_VERDICT_TTL_S:.0f} s")
    finally:
        cpva.get_day = real_get_day
        cpva.value_at_or_before = real_before
        cpva.invalidate_step_verdicts()


def live() -> None:
    print("[3] the real archiver")
    now = int(time.time() * 1e9)
    gdd = "L3-SPFE-AOD03-002:Order2_RB"
    ptm1 = "HAPLS-ENER_IN_PTM1_LT7_DIAG2:Energy"

    try:
        head = cpva.value_at_or_before(gdd, now, timeout=20.0)
    except Exception as exc:                     # noqa: BLE001
        print(f"  SKIP  archiver not reachable ({exc})")
        return
    if head.value is None:
        print("  SKIP  the GDD channel has no data at all")
        return
    age_h = (now - (head.ts_ns or now)) / 1e9 / 3600
    print(f"        GDD last written {age_h:.1f} h ago, value {head.value}")

    cpva.invalidate_step_verdicts()
    check(cpva.classify_step_channel(gdd, now, timeout=20.0),
          "the GDD setting is recognised as written-on-change")

    res = cpva.lookup_near(gdd, now, window_ns=300_000_000, prefer="nearest",
                           pending_if_uncovered=True, timeout=20.0)
    check(res.value == head.value and res.status in ("ok", "stale"),
          "the Slider's own lookup now returns the GDD value",
          f"{res.value} ({res.status})")

    e = cpva.value_at_or_before(ptm1, now, timeout=30.0)
    if e.value is None:
        print("  SKIP  no energy data to check the other half against")
        return
    cpva.invalidate_step_verdicts()
    check(not cpva.classify_step_channel(ptm1, now, timeout=30.0),
          "a per-shot energy channel is NOT held forward")
    # A moment far from any shot must still refuse to answer with a neighbour's value.
    quiet = (e.ts_ns or now) - 3 * 3600 * SEC
    res = cpva.lookup_near(ptm1, quiet, window_ns=300_000_000, prefer="nearest",
                           pending_if_uncovered=True, timeout=30.0)
    check(res.value is None or abs((res.ts_ns or 0) - quiet) <= 300_000_000,
          "an energy PV still says nothing rather than a neighbouring shot",
          f"{res.value} ({res.status})")


offline()
offline_classifier()
live()
print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
