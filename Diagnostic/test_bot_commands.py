"""Unit tests for the bot command language (no Qt, no network, no real clock).

Run with:  python test_bot_commands.py
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))

from bot_commands import (  # noqa: E402
    CommandError, parse_command, parse_plot_options, parse_time_spec,
    parse_yaxis_spec,
)

TZ = ZoneInfo("Europe/Prague")
NS = 1_000_000_000


def at(y, mo, d, h, mi=0) -> int:
    """A fixed 'now' in nanoseconds — the tests never read the real clock."""
    return int(datetime(y, mo, d, h, mi, tzinfo=TZ).timestamp() * NS)


NOW = at(2026, 8, 17, 14, 30)          # Monday afternoon


def span(spec, now=NOW):
    tr = parse_time_spec(spec, now, TZ)
    return (datetime.fromtimestamp(tr.start_ns / NS, tz=TZ),
            datetime.fromtimestamp(tr.end_ns / NS, tz=TZ))


def rejects(spec, parse=None):
    """True if the parser refuses `spec` with a chat-ready message."""
    parse = parse or (lambda s: parse_time_spec(s, NOW, TZ))
    try:
        parse(spec)
    except CommandError:
        return True
    return False


def close(a, b, tol=1e-6):
    return abs(a - b) < tol


# --- splitting -------------------------------------------------------------

def test_comma_separates_items():
    pc = parse_command("/plot chiller 1, chiller 2, chiller 3")
    assert pc.cmd == "/plot"
    assert pc.items == ["chiller 1", "chiller 2", "chiller 3"]
    assert pc.options == []


def test_semicolon_separates_options():
    pc = parse_command("/plot chiller 1, chiller 2; 7-18; y 15-35")
    assert pc.items == ["chiller 1", "chiller 2"]
    assert pc.options == ["7-18", "y 15-35"]


def test_single_item_still_works():
    pc = parse_command("/plot Chiller 1")
    assert pc.items == ["Chiller 1"] and pc.first == "Chiller 1"


def test_command_is_lowercased_but_items_keep_case():
    pc = parse_command("/PLOT Chiller 1")
    assert pc.cmd == "/plot" and pc.items == ["Chiller 1"]


def test_blank_items_and_trailing_separators_are_dropped():
    pc = parse_command("/plot a, , b, ;  ; 12h")
    assert pc.items == ["a", "b"] and pc.options == ["12h"]


def test_no_arguments():
    pc = parse_command("/status")
    assert pc.items == [] and pc.options == [] and pc.args == ""


# --- relative windows ------------------------------------------------------

def test_relative_windows_end_now():
    for spec, hours in (("12h", 12), ("12", 12), ("1.5h", 1.5),
                        ("90m", 1.5), ("2d", 48)):
        tr = parse_time_spec(spec, NOW, TZ)
        assert tr.end_ns == NOW, spec
        assert close(tr.hours, hours), (spec, tr.hours)


def test_zero_window_rejected():
    assert rejects("0h")


# --- clock windows ---------------------------------------------------------

def test_clock_window_uses_today_and_stops_at_now():
    start, end = span("7-18")
    assert (start.hour, start.minute) == (7, 0)
    assert end == datetime.fromtimestamp(NOW / NS, tz=TZ)   # clipped to now
    assert start.date() == end.date()


def test_finished_clock_window_is_not_clipped():
    start, end = span("7-12")
    assert (start.hour, end.hour) == (7, 12)


def test_minutes_are_honoured():
    start, _ = span("7:45-12:15")
    assert (start.hour, start.minute) == (7, 45)


def test_window_that_has_not_started_yet_means_yesterday():
    start, end = span("7-18", now=at(2026, 8, 17, 6, 0))
    assert start.day == 16 and end.day == 16
    assert (start.hour, end.hour) == (7, 18)


def test_window_crossing_midnight():
    start, end = span("22-6")
    assert (start.day, start.hour) == (16, 22)
    assert (end.day, end.hour) == (17, 6)


def test_hour_24_is_end_of_day():
    start, end = span("yesterday 20-24")
    assert (start.day, start.hour) == (16, 20)
    assert (end.day, end.hour) == (17, 0)


def test_invalid_clock_rejected():
    assert rejects("7-99")


def test_gibberish_rejected():
    assert rejects("sometime around lunch")


# --- named days and dates --------------------------------------------------

def test_today_runs_from_midnight_to_now():
    start, end = span("today")
    assert (start.day, start.hour) == (17, 0)
    assert end == datetime.fromtimestamp(NOW / NS, tz=TZ)


def test_yesterday_is_the_whole_day():
    start, end = span("yesterday")
    assert (start.day, start.hour) == (16, 0)
    assert (end.day, end.hour) == (17, 0)


def test_named_day_with_clock_window():
    start, end = span("yesterday 7-18")
    assert (start.day, start.hour, end.day, end.hour) == (16, 7, 16, 18)


def test_dates_with_clock_window():
    for spec in ("2026-08-15 7-18", "15.8. 7-18", "15.8.2026 7-18"):
        start, end = span(spec)
        assert (start.month, start.day, start.hour) == (8, 15, 7), spec
        assert (end.month, end.day, end.hour) == (8, 15, 18), spec


def test_bare_day_month_never_lands_in_the_future():
    start, _ = span("20.12. 7-18")      # December has not happened yet in 2026
    assert start.year == 2025


def test_date_only_is_the_whole_day():
    start, end = span("2026-08-15")
    assert (start.day, start.hour) == (15, 0)
    assert (end.day, end.hour) == (16, 0)


def test_future_window_rejected():
    assert rejects("2026-09-01 7-18")


# --- Y range ---------------------------------------------------------------

def test_yaxis_specs():
    cases = (("y 10-30", (10.0, 30.0)), ("y 10 30", (10.0, 30.0)),
             ("y -5-5", (-5.0, 5.0)), ("yaxis 1.5-2.5", (1.5, 2.5)),
             ("y auto", None))
    for spec, expected in cases:
        assert parse_yaxis_spec(spec) == expected, spec


def test_yaxis_must_go_low_to_high():
    assert rejects("y 30-10", parse_yaxis_spec)


def test_yaxis_gibberish_rejected():
    assert rejects("y wide", parse_yaxis_spec)


# --- options together ------------------------------------------------------

def test_plot_options_take_time_and_yaxis_in_any_order():
    opts = parse_plot_options(["y 15-35", "7-18"], NOW, TZ)
    assert opts.yaxis == (15.0, 35.0)
    assert close(opts.time.hours, 7.5)


def test_no_options_means_no_overrides():
    opts = parse_plot_options([], NOW, TZ)
    assert opts.time is None and opts.yaxis is None


def test_yesterday_is_not_mistaken_for_a_y_option():
    opts = parse_plot_options(["yesterday"], NOW, TZ)
    assert opts.yaxis is None and opts.time is not None


def test_bad_option_reports_itself():
    try:
        parse_plot_options(["next tuesday"], NOW, TZ)
    except CommandError as e:
        assert "next tuesday" in str(e)
    else:
        raise AssertionError("bad option was accepted")


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
