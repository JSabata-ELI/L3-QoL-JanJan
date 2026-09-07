"""The time-window language: a range between two points, and no regressions.

The first half is a regression table of every form that already worked, so the
range grammar cannot quietly capture one of them. No Qt, no network, no real
clock.

Run with:  python testing/test_time_ranges.py
"""

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot_commands import (  # noqa: E402
    CommandError, parse_plot_options, parse_time_spec,
)

TZ = ZoneInfo("Europe/Prague")
NS = 1_000_000_000


def at(y, mo, d, h=0, mi=0) -> int:
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp() * NS)


NOW = at(2026, 9, 2, 14, 30)           # Wednesday afternoon


def span(spec, now=NOW):
    tr = parse_time_spec(spec, now, TZ)
    return (datetime.fromtimestamp(tr.start_ns / NS, tz=TZ),
            datetime.fromtimestamp(tr.end_ns / NS, tz=TZ))


def label(spec, now=NOW):
    return parse_time_spec(spec, now, TZ).label


def rejects(spec, now=NOW):
    try:
        parse_time_spec(spec, now, TZ)
    except CommandError:
        return True
    return False


def dt(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


# --- nothing that already worked may change --------------------------------

def test_existing_forms_are_unchanged():
    y = dt(2026, 9, 1)                       # yesterday
    today = dt(2026, 9, 2)
    table = {
        "12h":              (dt(2026, 9, 2, 2, 30), dt(2026, 9, 2, 14, 30)),
        "12":               (dt(2026, 9, 2, 2, 30), dt(2026, 9, 2, 14, 30)),
        "90m":              (dt(2026, 9, 2, 13, 0), dt(2026, 9, 2, 14, 30)),
        "2d":               (dt(2026, 8, 31, 14, 30), dt(2026, 9, 2, 14, 30)),
        "7-18":             (dt(2026, 9, 2, 7), dt(2026, 9, 2, 14, 30)),
        "7:30-18:00":       (dt(2026, 9, 2, 7, 30), dt(2026, 9, 2, 14, 30)),
        "22-6":             (dt(2026, 9, 1, 22), dt(2026, 9, 2, 6)),
        "today":            (today, dt(2026, 9, 2, 14, 30)),
        "yesterday":        (y, dt(2026, 9, 2)),
        "today 7-18":       (dt(2026, 9, 2, 7), dt(2026, 9, 2, 14, 30)),
        "yesterday 7-18":   (dt(2026, 9, 1, 7), dt(2026, 9, 1, 18)),
        "15.8.":            (dt(2026, 8, 15), dt(2026, 8, 16)),
        "15.8.2026":        (dt(2026, 8, 15), dt(2026, 8, 16)),
        "15.8. 7-18":       (dt(2026, 8, 15, 7), dt(2026, 8, 15, 18)),
        "2026-08-15":       (dt(2026, 8, 15), dt(2026, 8, 16)),
        "2026-08-15 7-18":  (dt(2026, 8, 15, 7), dt(2026, 8, 15, 18)),
    }
    for spec, want in table.items():
        got = span(spec)
        assert got == want, f"{spec}: got {got}, want {want}"


def test_gibberish_still_reports_itself():
    for spec in ("next tuesday", "", "the day before yesterday"):
        assert rejects(spec), spec
    try:
        parse_time_spec("next tuesday", NOW, TZ)
    except CommandError as e:
        assert "next tuesday" in str(e)


# --- the new two-point range ------------------------------------------------

def test_dated_range_with_times():
    assert span("1.1. 9:00 - 1.9. 12:00") == (dt(2026, 1, 1, 9),
                                              dt(2026, 9, 1, 12))


def test_iso_dates_are_not_confused_by_their_own_dashes():
    assert span("2026-01-01 9:00 - 2026-09-01 12:00") == (dt(2026, 1, 1, 9),
                                                          dt(2026, 9, 1, 12))


def test_bare_dates_mean_whole_days_and_include_the_last_one():
    assert span("1.1. - 1.9.") == (dt(2026, 1, 1), dt(2026, 9, 2))


def test_no_spaces_around_the_dash():
    assert span("1.1.-1.9.") == (dt(2026, 1, 1), dt(2026, 9, 2))


def test_a_side_without_a_date_borrows_the_other_side_s_date():
    assert span("15.8. 9:00 - 22:00") == (dt(2026, 8, 15, 9),
                                          dt(2026, 8, 15, 22))
    assert span("9:00 - 15.8. 22:00") == (dt(2026, 8, 15, 9),
                                          dt(2026, 8, 15, 22))


def test_named_days_work_as_endpoints():
    assert span("yesterday 22:00 - today 6:00") == (dt(2026, 9, 1, 22),
                                                    dt(2026, 9, 2, 6))


def test_now_is_allowed_on_the_right():
    assert span("yesterday 9:00 - now") == (dt(2026, 9, 1, 9),
                                            dt(2026, 9, 2, 14, 30))


def test_range_may_cross_the_new_year():
    assert span("15.12. 9:00 - 5.1. 12:00") == (dt(2025, 12, 15, 9),
                                                dt(2026, 1, 5, 12))


def test_a_range_running_into_the_future_is_clipped_to_now():
    start, end = span("1.9. 9:00 - 5.9. 12:00")
    assert (start, end) == (dt(2026, 9, 1, 9), dt(2026, 9, 2, 14, 30))
    assert "so far" in label("1.9. 9:00 - 5.9. 12:00")


def test_backwards_range_is_refused_not_rolled_forward():
    assert rejects("1.9. 12:00 - 1.9. 9:00")
    try:
        parse_time_spec("1.9. 12:00 - 1.9. 9:00", NOW, TZ)
    except CommandError as e:
        assert "ends before it starts" in str(e)


def test_ranges_longer_than_three_years_are_refused():
    assert rejects("1.1.2020 - 1.1.2026")
    assert rejects("5000d")
    assert not rejects("180d")


def test_whole_day_end_is_labelled_by_the_day_the_user_typed():
    lab = label("1.1. - 1.9.")
    assert "2026-09-01" in lab and "2026-09-02" not in lab
    assert "end of the day" in lab


def test_hour_24_ends_the_day():
    assert span("15.8. 9:00 - 15.8. 24:00") == (dt(2026, 8, 15, 9),
                                                dt(2026, 8, 16))


# --- daylight saving --------------------------------------------------------
#
# Measured in nanoseconds on purpose: subtracting two aware datetimes that share
# one tzinfo object ignores the offset and would always answer 24 h.

def hours(spec):
    tr = parse_time_spec(spec, NOW, TZ)
    return (tr.end_ns - tr.start_ns) / 3600e9


def test_spring_forward_day_is_23_hours():
    assert hours("29.3.2026 - 29.3.2026") == 23.0


def test_autumn_back_day_is_25_hours():
    assert hours("26.10.2025 - 26.10.2025") == 25.0


def test_a_range_across_the_spring_gap_keeps_wall_clock_times():
    start, end = span("29.3.2026 1:00 - 29.3.2026 5:00")
    assert start == dt(2026, 3, 29, 1) and end == dt(2026, 3, 29, 5)
    # 02:00-03:00 does not exist that morning, so four hours on the clock are
    # three hours of readings.
    assert hours("29.3.2026 1:00 - 29.3.2026 5:00") == 3.0


# --- the detail option ------------------------------------------------------

def test_detail_is_recognised_and_is_not_a_time_window():
    opts = parse_plot_options(["180d", "detail"], NOW, TZ)
    assert opts.detail is True
    assert opts.time is not None and opts.time.hours == 180 * 24


def test_detail_synonyms():
    for word in ("detail", "Details", " FULL ", "raw"):
        assert parse_plot_options([word], NOW, TZ).detail is True


def test_without_detail_the_flag_stays_off():
    assert parse_plot_options(["7-18"], NOW, TZ).detail is False


def test_two_time_windows_are_flagged_instead_of_silently_dropped():
    opts = parse_plot_options(["7-18", "15.8."], NOW, TZ)
    assert opts.warnings and "15.8." in opts.warnings[0]
    assert opts.time.start_ns == at(2026, 8, 15)


def test_a_range_survives_the_option_parser():
    opts = parse_plot_options(["1.1. 9:00 - 1.9. 12:00", "y 0-10"], NOW, TZ)
    assert opts.time.start_ns == at(2026, 1, 1, 9)
    assert opts.yaxis == (0.0, 10.0)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    fails = 0
    for f in fns:
        try:
            f()
            print("PASS", f.__name__)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL", f.__name__, "->", repr(e))
    print("---")
    print(f"{len(fns) - fails}/{len(fns)} passed")
    sys.exit(1 if fails else 0)
