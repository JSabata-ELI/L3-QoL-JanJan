"""The shot filter against the REAL archive, with no GUI.

The one thing a synthetic test cannot check: that the value held forward onto
each shot is the value the archive really holds, and that filtering on it splits
a stretch of shots the way the GDD timeline says it should.

It reads a short window (default 6 minutes) of the spectrum channel and of
L3-SPFE-AOD03-002:Order2_RB, holds the GDD forward onto every shot, and prints
how many shots fall at each setting. Deliberately short: a raw read of a fast
waveform PV over hours is what once drove this PC into swap.

    python testing/probe_filter_real_archive.py                 (last GDD change)
    python testing/probe_filter_real_archive.py 2026-09-21 14:40 6

Needs the archiver at 10.78.0.57, so it fails cleanly off-site.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np                                   # noqa: E402

import sp_t                                          # noqa: E402

PRAGUE = ZoneInfo("Europe/Prague")
GDD = "L3-SPFE-AOD03-002:Order2_RB"


def _ns(dt):
    return int(dt.timestamp() * 1e9)


def _find_window(minutes: int):
    """A window around the most recent GDD change in the last week."""
    now = datetime.now(timezone.utc)
    series = sp_t._fetch_scalars(GDD, _ns(now - timedelta(days=7)), _ns(now))
    if len(series) < 2:
        print(f"  {GDD}: {len(series)} samples in the last week — "
              "picking the last 6 minutes instead")
        return now - timedelta(minutes=minutes), now
    changes = [t for (t, v), (_, pv) in zip(series[1:], series[:-1]) if v != pv]
    if not changes:
        return now - timedelta(minutes=minutes), now
    t_step = datetime.fromtimestamp(changes[-1] / 1e9, timezone.utc)
    half = timedelta(minutes=minutes / 2)
    return t_step - half, t_step + half


def main():
    if len(sys.argv) >= 3:
        day, hhmm = sys.argv[1], sys.argv[2]
        minutes = int(sys.argv[3]) if len(sys.argv) > 3 else 6
        t0 = datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=PRAGUE)
        t1 = t0 + timedelta(minutes=minutes)
    else:
        t0, t1 = _find_window(6)

    start, end = _ns(t0), _ns(t1)
    print(f"window  {t0.astimezone(PRAGUE):%Y-%m-%d %H:%M:%S} … "
          f"{t1.astimezone(PRAGUE):%H:%M:%S} Prague")

    series = sp_t._fetch_scalars(GDD, start, end)
    print(f"\n{GDD}")
    print(f"  {len(series)} sample(s) inside the window "
          "(the first one is usually the archiver's freebie from BEFORE it)")
    for t, v in series[:8]:
        inside = "in " if t >= start else "before"
        print(f"    {inside} {datetime.fromtimestamp(t / 1e9, PRAGUE):%H:%M:%S}  {v!r}")
    if not series:
        print("    NOTHING AT ALL — that is the case the filter must reject and "
              "name, not silently pass")

    ch = sp_t.PV_SPEC_Y
    wfs = sp_t._fetch_waveforms(ch, start, end)
    print(f"\n{ch}")
    print(f"  {len(wfs)} shot(s)")
    if not wfs:
        print("  no spectra in this window — nothing to filter")
        return 0

    common = sp_t._modal_length([a for _, a in wfs])
    ts = np.asarray([t for t, a in wfs if len(a) == common], dtype=np.int64)
    print(f"  {len(ts)} of them at the modal length {common}")

    vals = sp_t._hold_forward(series, ts)
    n_nan = int(np.count_nonzero(~np.isfinite(vals)))
    print(f"\nGDD held forward onto each shot: {len(vals)} values, {n_nan} unknown")
    for v in sorted({float(x) for x in vals if np.isfinite(x)}):
        m = sp_t._match_value(vals, v, 0.0)
        first = datetime.fromtimestamp(int(ts[np.flatnonzero(m)[0]]) / 1e9, PRAGUE)
        last = datetime.fromtimestamp(int(ts[np.flatnonzero(m)[-1]]) / 1e9, PRAGUE)
        print(f"  GDD = {sp_t.SpectraWidget._fmt_full(v):>10}  ± 0  -> "
              f"{int(m.sum()):5d} shot(s)   {first:%H:%M:%S}…{last:%H:%M:%S}")
    settled = sum(int(sp_t._match_value(vals, v, 0.0).sum())
                  for v in {float(x) for x in vals if np.isfinite(x)})
    print(f"\n  every shot accounted for exactly once: "
          f"{settled} == {len(vals) - n_nan}"
          + ("  OK" if settled == len(vals) - n_nan else "  MISMATCH"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
