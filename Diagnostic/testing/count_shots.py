"""Count how many times the pump diodes of PC1..PC4 have fired, from the archiver.

HOW IT WORKS
------------
Each pump-diode energy meter reports a reading on every system trigger, and the
archiver stores a reading whenever it differs from the one before. Measured
12 Sep 2025 and 3 Sep 2026:

  * while the diodes fire, the reading differs every shot, so essentially every
    shot is stored (58..61 stored per 60 expected in five-minute blocks);
  * while they are dark the reading only toggles between two quantised values,
    so most repeats are dropped -- but every gap between two stored readings is
    an exact multiple of the trigger period (5.0 s at SysRate 0.2, 0.3 s at
    3.333).

So the count is not "samples above the threshold": it is, for every pair of
consecutive readings, the number of triggers the earlier reading was held for,
added up wherever that reading was above the firing threshold. The trigger
period is read out of the data itself (the shortest gap actually present),
which is why SysRate never has to be downloaded -- it is archived five times a
second and a year of it is tens of gigabytes.

A gap far longer than the trigger period is not a run of shots: it is an
archiver outage or a dead PV. Those are counted as one shot and their length is
reported separately, so an inflated answer cannot hide.

Only the stretches with the high-power key on are looked at, because the diodes
cannot fire while it is off. That is also what makes the query affordable.

USAGE
-----
    python count_shots.py                  # whole archive, from 2025-09-01
    python count_shots.py 2026-08-01 2026-09-05
Results are cached per day in shot_counts_cache.json, so a re-run is cheap.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cpva_api as C

NS = 1_000_000_000
TZ = ZoneInfo("Europe/Prague")

# TWO archived copies of one energy meter per pulser chassis, not two meters.
#
# Measured 3 Sep 2026, 10:00, one PD1M1 minute under both names: the same 23
# readings, agreeing to the last decimal place, with timestamps that differ by
# tens of milliseconds and never coincide. So they are one stream sampled twice
# and the union of the two would count every shot twice.
#
# Neither copy is complete, and they fail on different days:
#   4 Aug 2026 - the L3- copy of PD2, PD3 and PD4 is empty all day, while the
#                HAPLS- copy holds 11 583 / 84 603 / 83 340 readings;
#   28 Aug 2026 - the other way round for PD3: 9 398 under L3-, none at all
#                under HAPLS-;
#   4 Aug 2026, PD1 - both written, but 98 367 readings against 77 229.
#
# So both are fetched and the FULLER one is counted. Taking one name on faith
# is what produced a day of "PC3 fired nothing" while it plainly had.
ALL_DIODES = {
    "PC1": ("HAPLS-ENER_IN_PD1M1_LT7_DIAG2:Energy", "L3-PD1M1-PM314:Energy"),
    "PC2": ("HAPLS-ENER_IN_PD2M1_LT7_DIAG2:Energy", "L3-PD2M1-PM316:Energy"),
    "PC3": ("HAPLS-ENER_IN_PD3M1_LT7_DIAG2:Energy", "L3-PD3M1-PM317:Energy"),
    "PC4": ("HAPLS-ENER_IN_PD4M1_LT7_DIAG2:Energy", "L3-PD4M1-PM319:Energy"),
}
# Which of them to count. All four arrays fire together in all but a fraction
# of a per cent of the time, so one array answers the question and two of them
# check each other; PC2's meter is the unreliable one and is left out by
# default. Override on the command line: --pc PC1,PC3
DIODES = {k: ALL_DIODES[k] for k in ("PC1", "PC3")}
HPE = "L3-SIS-KEY:HighPowerEnable"

FIRING_J = 10.0            # above this the diodes were firing
MAX_HELD_NS = 60 * NS      # a longer gap is an outage, not a run of shots

# The only two rates the timing system runs at. Checked against
# L3-TIMING-TIMING:SysRate - which is archived five times a second, so it hides
# nothing - across the whole year: it holds 0.2 or 3.333 and never anything
# else.
#
# The period read out of the gaps is therefore snapped to one of these. Without
# that, eight days of the year came out at 0.1, 3.4 or 3.9 Hz because the
# densest cluster of gaps landed on a multiple instead of the period itself,
# and a period read twice too long halves that stretch's count.
REAL_RATES = (0.2, 10.0 / 3.0)


def snap_rate(period_ns: int) -> int:
    """The nearest real rate's period. Ratios, not differences: the two rates
    are a factor of 17 apart, so a plain difference would drag everything to
    3.333."""
    rate = NS / period_ns
    best = min(REAL_RATES, key=lambda r: abs(math.log(rate / r)))
    return round(NS / best)

# The first diode reading in the archive, and so the earliest this question can
# be answered from. Not a guess: the archiver answers a window that predates a
# channel's data with the first sample AFTER it, so a one-minute window in 2010
# returns each channel's very first reading. Asked of all eight copies:
#
#   PD3M1 15 Aug 2025 12:42     PD1M1 27 Aug 2025 23:04
#   PD4M1 15 Aug 2025 15:09     PD2M1 30 Aug 2025 06:22
#
# Everything before 15 Aug 2025 is outside the archive, not merely quiet. The
# high-power key reaches back to 29 Mar 2022 and so do 211 other pulser
# channels, but no reading of the diodes' energy does.
ARCHIVE_START = datetime(2025, 8, 15, tzinfo=TZ)

CACHE = Path(__file__).with_name("shot_counts_cache.json")


# ── archiver helpers ─────────────────────────────────────────────────────────

def samples(channel: str, s_ns: int, e_ns: int,
            chunk_ns: int = C.CHUNK_SIZE_NS) -> list[tuple[int, float]]:
    """Numeric samples inside [s_ns, e_ns), sorted, duplicate timestamps dropped.

    The default one-hour chunk is sized for a channel written many times a
    second. A channel that only records its changes wants a far bigger chunk:
    the high-power key over 30 days is one small answer, but in hourly pieces
    it is 720 requests - which is what made the first full run appear to hang.
    """
    raw = C.cpva_fetch_samples_chunked(channel, s_ns, e_ns, timeout=60.0,
                                       max_workers=10, errors={},
                                       chunk_ns=chunk_ns)
    out = {}
    for x in raw:
        t = x.get("timestamp") or x.get("time")
        if t is None:
            continue
        t = int(t)
        if not (s_ns <= t < e_ns):
            continue
        v = C.cpva_decode_value(x)
        if isinstance(v, list) and v:
            v = v[0]
        if isinstance(v, (int, float)):
            out[t] = float(v)
    return sorted(out.items())


_HPE_SERIES: list[tuple[int, float]] = []


def load_hpe(s_ns: int, e_ns: int) -> None:
    """Fetch the whole high-power-key history once, for the whole run.

    Only transitions of this channel are archived and the gap between two of
    them has been measured at up to 11.6 days, so the run also reaches 30 days
    further back: the state a window opens in has to be found, not assumed.
    """
    global _HPE_SERIES
    _HPE_SERIES = samples(HPE, s_ns - 30 * 86400 * NS, e_ns,
                          chunk_ns=30 * 86400 * NS)


def hpe_on_spans(s_ns: int, e_ns: int) -> list[tuple[int, int]]:
    """Stretches of [s_ns, e_ns) with the high-power key on."""
    pre = [v for t, v in _HPE_SERIES if t < s_ns]
    state = pre[-1] if pre else 0.0
    spans, open_at = [], (s_ns if state >= 0.5 else None)
    for t, v in _HPE_SERIES:
        if not (s_ns <= t < e_ns):
            continue
        on = v >= 0.5
        if on and open_at is None:
            open_at = t
        elif not on and open_at is not None:
            spans.append((open_at, t))
            open_at = None
    if open_at is not None:
        spans.append((open_at, e_ns))
    return spans


# ── the count itself ─────────────────────────────────────────────────────────

def trigger_period_ns(pts: list[tuple[int, float]]) -> int | None:
    """The trigger period, read out of the gaps that are actually present.

    Not the shortest gap, and not the commonest one to the hundredth of a
    second: the timestamps jitter by a good ten per cent (measured 4 Aug 2026,
    a 3.333 Hz day: gaps spread over 0.24..0.36 s with no single value holding
    more than a quarter of them; 3 Sep 2026 at 0.2: 4.96..5.05 s). Taking the
    shortest gap that occurred "often enough" therefore read 0.14 s and
    doubled the answer.

    So the period is the middle of the densest CLUSTER of gaps: for each
    candidate, how many gaps sit within +/-15 % of it, and the median of the
    best one wins. Longer gaps are multiples of the period and form their own,
    smaller clusters, which is why the densest one is the period itself.
    """
    gaps = sorted((t2 - t1) for (t1, _), (t2, _) in zip(pts, pts[1:])
                  if t2 - t1 > NS // 100)      # ignore sub-10 ms duplicates
    if not gaps:
        return None

    import bisect

    best_n, best = 0, gaps[0]
    # Candidates from the data itself, thinned out so a long day stays cheap.
    step = max(1, len(gaps) // 400)
    for cand in gaps[::step]:
        lo = bisect.bisect_left(gaps, int(cand * 0.85))
        hi = bisect.bisect_right(gaps, int(cand * 1.15))
        if hi - lo > best_n:
            best_n, best = hi - lo, cand
    lo = bisect.bisect_left(gaps, int(best * 0.85))
    hi = bisect.bisect_right(gaps, int(best * 1.15))
    cluster = gaps[lo:hi]
    return cluster[len(cluster) // 2]


def count_span(pts: list[tuple[int, float]], span_end: int):
    """Shots, firing seconds and unreliable seconds inside one span.

    The period is worked out per hour rather than once for the whole span,
    because the rate does change during a shift - 12 Sep 2025 ran at 3.333
    until 09:20 and at 0.2 afterwards - and one period for the whole day would
    then be wrong for half of it.
    """
    if not pts:
        return 0, 0.0, 0.0, set()

    raw = trigger_period_ns(pts)
    day_period = snap_rate(raw) if raw else None
    by_hour: dict[int, list] = {}
    for t, v in pts:
        by_hour.setdefault(t // (3600 * NS), []).append((t, v))
    periods = {}
    for h, p in by_hour.items():
        if len(p) < 20:
            continue
        r = trigger_period_ns(p)
        periods[h] = snap_rate(r) if r else day_period

    shots = 0
    firing_ns = 0
    murky_ns = 0
    rates = set()
    for i, (t, v) in enumerate(pts):
        t_next = pts[i + 1][0] if i + 1 < len(pts) else span_end
        held = max(0, t_next - t)
        period = periods.get(t // (3600 * NS)) or day_period
        if not period:
            continue
        rates.add(round(NS / period, 2))
        if v <= FIRING_J:
            continue
        if held > MAX_HELD_NS:
            # An outage or a dead PV, not a run of shots: one shot is certain,
            # the rest of the stretch is reported as unreliable instead.
            murky_ns += held
            shots += 1
            continue
        shots += max(1, round(held / period))
        firing_ns += held
    return shots, firing_ns / NS, murky_ns / NS, rates


def do_day(day: datetime, cache: dict) -> dict:
    key = f"{day:%Y-%m-%d}"
    if key in cache:
        return cache[key]

    s = int(day.timestamp() * NS)
    e = int((day + timedelta(days=1)).timestamp() * NS)
    spans = hpe_on_spans(s, e)
    row = {"hpe_on_hours": sum(b - a for a, b in spans) / NS / 3600,
           "pc": {}}
    for pc, copies in DIODES.items():
        shots = 0
        firing = 0.0
        murky = 0.0
        rates: set = set()
        emax = 0.0
        no_data_h = 0.0
        for a, b in spans:
            pts = max((samples(ch, a, b) for ch in copies), key=len)
            if not pts:
                # High power was on and this meter wrote nothing at all: the
                # channel is out, not the diodes. Never counted as zero shots
                # without saying so.
                no_data_h += (b - a) / NS / 3600
                continue
            emax = max(emax, max(v for _, v in pts))
            n, f, m, r = count_span(pts, b)
            shots += n
            firing += f
            murky += m
            rates |= r
        row["pc"][pc] = {"shots": shots, "firing_h": round(firing / 3600, 3),
                         "unreliable_h": round(murky / 3600, 3),
                         "no_data_h": round(no_data_h, 3),
                         "rates": sorted(rates), "e_max": round(emax, 2)}
    cache[key] = row
    return row


def main() -> None:
    global DIODES
    args = sys.argv[1:]
    if "--pc" in args:
        i = args.index("--pc")
        DIODES = {k: ALL_DIODES[k] for k in args[i + 1].split(",")}
        del args[i:i + 2]
    start = (datetime.strptime(args[0], "%Y-%m-%d").replace(tzinfo=TZ)
             if args else ARCHIVE_START)
    end = (datetime.strptime(args[1], "%Y-%m-%d").replace(tzinfo=TZ)
           if len(args) > 1 else datetime.now(TZ).replace(
               hour=0, minute=0, second=0, microsecond=0))

    cache = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    load_hpe(int(start.timestamp() * NS), int(end.timestamp() * NS))
    totals = {pc: 0 for pc in DIODES}
    murky = {pc: 0.0 for pc in DIODES}
    blind = {pc: 0.0 for pc in DIODES}

    print(f"{'day':11s} {'HP h':>5s} " +
          " ".join(f"{pc:>9s}" for pc in DIODES) +
          "   rate Hz   blind hours (no reading while HP on)")
    d = start
    while d < end:
        row = do_day(d, cache)
        for pc in DIODES:
            totals[pc] += row["pc"][pc]["shots"]
            murky[pc] += row["pc"][pc]["unreliable_h"]
            blind[pc] += row["pc"][pc].get("no_data_h", 0.0)
        rates = sorted({r for pc in DIODES for r in row["pc"][pc]["rates"]})
        gaps = "  ".join(f"{pc}:{row['pc'][pc].get('no_data_h', 0):.1f}"
                         for pc in DIODES
                         if row["pc"][pc].get("no_data_h", 0) > 0.05)
        print(f"{d:%Y-%m-%d} {row['hpe_on_hours']:5.1f} " +
              " ".join(f"{row['pc'][pc]['shots']:9d}" for pc in DIODES) +
              "   " + ", ".join(f"{r:g}" for r in rates) + "   " + gaps)
        CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        d += timedelta(days=1)

    print("\nTOTAL " + "  ".join(f"{pc}={totals[pc]:,}" for pc in DIODES))
    print("hours held over an outage (counted as one shot): " +
          "  ".join(f"{pc}={murky[pc]:.1f}" for pc in DIODES))
    print("hours with high power on and no reading at all: " +
          "  ".join(f"{pc}={blind[pc]:.1f}" for pc in DIODES))


if __name__ == "__main__":
    main()
