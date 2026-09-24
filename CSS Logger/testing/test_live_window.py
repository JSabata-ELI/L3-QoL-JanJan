"""_live_span_ns — the stretch Live preloads, taken from the picked window.

Daypicker rule 7, the Image Slider's rule: the From time is kept and only the
DATE moves to today; "To" is thrown away because live has not happened yet.
Nothing picked, or a From still in the future, falls back to the last whole hour.

Live used to preload a flat ten minutes, which is why a filter set on a morning
value found nothing — the morning had never been read.

Qt-free: _live_span_ns is a module function and `now` is injectable.

    python testing/test_live_window.py
"""
import os
import sys
from datetime import date, datetime, timedelta

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import sp_t                                          # noqa: E402

FAILED = []
TZ = sp_t.TZ_PRAGUE_DP


def check(name, ok, detail=""):
    print(f"  {'OK  ' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILED.append(name)


def span(segments, windows, now):
    a, b = sp_t._live_span_ns(segments, windows, now)
    return sp_t._ns_to_dt(a), sp_t._ns_to_dt(b)


def main():
    now = datetime(2026, 9, 23, 12, 20, 31, tzinfo=TZ)
    today = date(2026, 9, 23)
    yesterday = date(2026, 9, 22)

    # ── A window picked on a past day: the hours stay, the date moves ────
    a, b = span([sp_t.PickSeg(yesterday, 7, 30, 21, 0)], [], now)
    check("the picked From is kept", (a.hour, a.minute) == (7, 30),
          a.strftime("%H:%M"))
    check("the date moves to today", a.date() == today, str(a.date()))
    check("the end is now, not the picked To", (b.hour, b.minute) == (12, 20),
          b.strftime("%H:%M"))

    # ── Today already in the pick: that day's own From wins ─────────────
    a, _b = span([sp_t.PickSeg(yesterday, 6, 0, 21, 0),
                  sp_t.PickSeg(today, 9, 15, 21, 0)], [], now)
    check("today's own From is used", (a.hour, a.minute) == (9, 15),
          a.strftime("%H:%M"))

    # ── Several past days, none of them today: the last one wins ────────
    a, _b = span([sp_t.PickSeg(date(2026, 9, 20), 6, 0, 21, 0),
                  sp_t.PickSeg(date(2026, 9, 21), 8, 45, 21, 0)], [], now)
    check("the last day picked is the one", (a.hour, a.minute) == (8, 45),
          a.strftime("%H:%M"))

    # ── Nothing picked at all: the last whole hour ──────────────────────
    a, b = span([], [], now)
    check("nothing picked starts at the top of this hour",
          (a.hour, a.minute) == (12, 0), a.strftime("%H:%M"))
    check("and still ends now", (b.hour, b.minute) == (12, 20),
          b.strftime("%H:%M"))

    # ── A From still in the future: the last whole hour, not tomorrow ───
    a, _b = span([sp_t.PickSeg(yesterday, 14, 0, 21, 0)], [], now)
    check("a From later than now falls back to this hour",
          (a.hour, a.minute) == (12, 0), a.strftime("%H:%M"))

    # ── No segments but a loaded window: read the From off the window ───
    w = sp_t.seg_bounds_ns(sp_t.PickSeg(yesterday, 10, 5, 18, 0))
    a, _b = span([], [w], now)
    check("a bare window gives its From", (a.hour, a.minute) == (10, 5),
          a.strftime("%H:%M"))

    # ── Right after midnight the hour is 00:00, never the day before ────
    midnight = datetime(2026, 9, 23, 0, 4, 0, tzinfo=TZ)
    a, b = span([], [], midnight)
    check("00:04 falls back to 00:00 today",
          (a.date(), a.hour, a.minute) == (today, 0, 0),
          a.strftime("%Y-%m-%d %H:%M"))
    check("the span is never negative", b >= a)

    # ── The span the >2 h question is measured on ───────────────────────
    a_ns, b_ns = sp_t._live_span_ns([sp_t.PickSeg(yesterday, 7, 0, 21, 0)],
                                    [], now)
    hours = (b_ns - a_ns) / 3.6e12
    check("07:00 to 12:20 is 5.3 h, so the question is asked",
          abs(hours - 5.34) < 0.02 and hours > sp_t._LIVE_PRELOAD_WARN_H,
          f"{hours:.2f} h")
    a_ns, b_ns = sp_t._live_span_ns([], [], now)
    check("the last-hour fallback is under the question's limit",
          (b_ns - a_ns) / 3.6e12 <= sp_t._LIVE_PRELOAD_WARN_H,
          f"{(b_ns - a_ns) / 3.6e12:.2f} h")

    # ── The buffer cap has to hold a lab day ────────────────────────────
    check("the buffer holds a whole lab day of shots",
          sp_t.LIVE_BUF_MAX >= 14 * 460, str(sp_t.LIVE_BUF_MAX))

    # ── A legacy (date, hour_from, hour_to) tuple still works ───────────
    a, _b = span([(yesterday, 8, 20)], [], now)
    check("a legacy segment tuple is accepted", (a.hour, a.minute) == (8, 0),
          a.strftime("%H:%M"))

    # ── A real "yesterday" relative to the clock, as _enter_live sees it ─
    real_now = datetime.now(TZ)
    a, b = span([sp_t.PickSeg(real_now.date() - timedelta(days=1), 7, 0, 21, 0)],
                [], real_now)
    check("with the real clock the window still lands on today",
          a.date() == real_now.date() and (a.hour, a.minute) == (7, 0),
          a.strftime("%Y-%m-%d %H:%M"))

    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: " + ", ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
