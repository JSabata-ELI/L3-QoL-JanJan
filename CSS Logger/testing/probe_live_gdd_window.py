"""The reported case, end to end against the real archive.

2026-09-23: the user set the shot filter to GDD = 24700 in live mode and got
"nothing matches", although the graph clearly showed several dozen shots between
09:20 and 09:40 at that setting. Live preloaded a flat ten minutes and applied
"average last N" BEFORE the filter, so the morning was never in the buffer.

This probe replays the whole live path on today's archive:
  1. reads the span _live_span_ns gives for a 07:00 pick,
  2. fills _live_buf and _live_scalars from the archive exactly as _live_tick
     and _absorb_live_scalars do,
  3. asks _live_shots for the last N at several GDD settings.

It reads the archive, so it is a probe, not a unit test.

    python testing/probe_live_gdd_window.py
"""
import os
import sys
from collections import deque
from datetime import date, datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PySide6.QtWidgets import QApplication           # noqa: E402

import sp_t                                          # noqa: E402

GDD = "L3-SPFE-AOD03-002:Order2_RB"
TZ = sp_t.TZ_PRAGUE_DP
DAY = date.today()
FROM_HOUR = 7


def main():
    app = QApplication.instance() or QApplication([])   # noqa: F841
    w = sp_t.SpectraWidget()

    # 1 ── the span a 07:00 pick gives Live
    w._segments = [sp_t.PickSeg(DAY - timedelta(days=1), FROM_HOUR, 0, 21, 0)]
    w._windows = [sp_t.seg_bounds_ns(w._segments[0])]
    start_ns, now_ns = sp_t._live_span_ns(w._segments, w._windows)
    print(f"Live span: {sp_t._fmt_date(start_ns)} "
          f"{sp_t._fmt_hms(start_ns)} → {sp_t._fmt_hms(now_ns)}  "
          f"({(now_ns - start_ns) / 3.6e12:.2f} h)")

    # 2 ── fill the buffer the way the live poll does
    t = datetime.now()
    wfs = sp_t._fetch_waveforms(sp_t.PV_SPEC_Y, start_ns, now_ns)
    series = sp_t._fetch_scalars(GDD, start_ns, now_ns)
    print(f"read {len(wfs)} spectra and {len(series)} GDD samples "
          f"in {(datetime.now() - t).total_seconds():.1f} s")
    if not wfs:
        print("NO SPECTRA IN THE WINDOW — nothing to check")
        return 1
    print(f"first shot {sp_t._fmt_hms(wfs[0][0])}, "
          f"last {sp_t._fmt_hms(wfs[-1][0])}")
    if len(wfs) > sp_t.LIVE_BUF_MAX:
        print(f"!! {len(wfs)} shots is past LIVE_BUF_MAX={sp_t.LIVE_BUF_MAX}")

    w._live = True
    w._live_buf = deque(((int(ts), a) for ts, a in wfs),
                        maxlen=sp_t.LIVE_BUF_MAX)
    w._live_scalars = {GDD: [(int(ts), float(v)) for ts, v in series]}

    # 3 ── what each GDD setting finds, at N = 200
    settings = sorted({round(float(v), 3) for _t, v in series})
    print(f"\nGDD settings seen in the window: {settings}")
    print(f"\n{'GDD':>12}  {'matched':>8}  {'used (N=200)':>13}  window")
    for value in settings:
        w._filter_on = True
        w._filter_conds = [{"label": "GDD", "channel": GDD, "value": value,
                            "tol": 0.0, "on": True}]
        got = w._live_shots(200)
        span = (f"{sp_t._fmt_hms(got[0][0])}–{sp_t._fmt_hms(got[-1][0])}"
                if got else "—")
        print(f"{value:>12}  {w._live_matched_n:>8}  {len(got):>13}  {span}")

    # the morning setting must be reachable
    w._filter_conds = [{"label": "GDD", "channel": GDD, "value": 24700.0,
                        "tol": 0.0, "on": True}]
    got = w._live_shots(200)
    print()
    if got:
        print(f"PASS  GDD = 24700 finds {w._live_matched_n} shots in the window, "
              f"the last {len(got)} are drawn "
              f"({sp_t._fmt_hms(got[0][0])}–{sp_t._fmt_hms(got[-1][0])})")
        return 0
    print("FAIL  GDD = 24700 still finds nothing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
