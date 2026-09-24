"""`/help plot` — the long help for one command.

The cheat sheet has to stay skimmable on a phone, so the examples and the traps
of each command live in their own page. What is checked here:

* every command the bot answers has a page (a new command without one is the
  failure this catches);
* the page for /plot really carries the traps that have caught somebody - the
  band being tiny, SAMPLED, a bare number meaning hours, /cancel;
* the words people actually type reach the right page (`/help dpi`,
  `plot help`, `/help ALARM`);
* an unknown topic says what there is instead of going quiet.

Run with:  python testing/test_help_topics.py
"""

from __future__ import annotations

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import bot_commands as bc  # noqa: E402
import monitor_tab as mt  # noqa: E402
import okbase_menu as om  # noqa: E402

# Every command in _handle_command. /help and /food are answered from elsewhere
# (the cheat sheet itself, and okbase_menu.FOOD_HELP), so they carry no page of
# their own.
COMMANDS = [
    "/status", "/alarms", "/list", "/plot", "/cancel", "/abort", "/nevermind",
    "/start", "/stop", "/window", "/yaxis", "/graph", "/enable", "/disable",
    "/datawatchdog", "/run", "/rundiagnostic", "/change", "/limit", "/limits",
    "/undo",
]

# The cheat sheet is meant to be read on a phone at the machine. It has run to
# several screens twice now, so the ceiling is a test rather than an intention.
HELP_MAX_CHARS = 2600


def _win():
    """Stand-in for MonitorWidget carrying only what the help reply reads."""
    win = types.SimpleNamespace(
        settings=dict(mt.DEFAULT_SETTINGS),
        hub=types.SimpleNamespace(
            webex=types.SimpleNamespace(bot_name="Diagnostics")))
    win._cmd_help_topic = types.MethodType(mt.MonitorWidget._cmd_help_topic, win)
    win._cmd_help = types.MethodType(mt.MonitorWidget._cmd_help, win)
    return win


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def test_every_command_has_a_page():
    missing = [c for c in COMMANDS if not bc.command_help(c)]
    assert not missing, f"no /help page for {missing}"


def test_food_is_answered_from_its_own_help():
    for word in ("food", "menu", "lunch"):
        assert _win()._cmd_help_topic(word) == om.FOOD_HELP


# ---------------------------------------------------------------------------
# What the /plot page must say
# ---------------------------------------------------------------------------

def test_the_plot_page_carries_the_traps():
    page = bc.command_help("plot")
    for must in ("1 %", "SAMPLED", "300dpi", "`/cancel`", "detail", "hours"):
        assert must in page, must
    # The one that was actually asked in the chat: a bare number is a window.
    assert "bare number" in page.lower()
    # And the examples are real commands, not prose.
    assert "/plot Chiller 1, Chiller 2; yesterday 7-18" in page
    assert "/plot all" in page


def test_the_plot_page_names_the_default_resolution():
    page = bc.command_help("plot")
    assert str(mt.CHART_DPI) in page
    assert f"{bc.DPI_MIN}-{bc.DPI_MAX}" in page


def test_the_status_page_explains_off_and_not_refreshed():
    page = bc.command_help("status")
    assert "[off]" in page and "not refreshed" in page
    assert "4 of 31" in page          # the group filter, with its caption


# ---------------------------------------------------------------------------
# Finding the page
# ---------------------------------------------------------------------------

def test_the_words_people_type_reach_the_page():
    for text, topic in (("/plot", "plot"), ("plot", "plot"), ("PLOT", "plot"),
                        ("plot?", "plot"), ("dpi", "plot"),
                        ("resolution", "plot"), ("sampled", "plot"),
                        ("/alarms", "alarms"), ("ALARM", "alarms"),
                        ("alerts", "alarms"), ("abort", "cancel"),
                        ("start", "stop"), ("disable", "enable"),
                        ("y", "yaxis"), ("watchdog", "datawatchdog"),
                        ("rundiagnostic", "run"), ("pvs", "list")):
        assert bc.help_topic(text) == topic, text


def test_a_command_with_arguments_still_finds_its_page():
    assert _win()._cmd_help_topic("plot Chiller 1; 12h") \
        == bc.command_help("plot")


def test_an_unknown_topic_says_what_there_is():
    reply = _win()._cmd_help_topic("banana")
    assert "banana" in reply
    for topic in bc.help_topics():
        assert f"`{topic}`" in reply
    assert "`/help`" in reply


def test_bare_help_is_not_a_topic():
    assert bc.help_topic("") == "" and bc.help_topic("help") == ""


# ---------------------------------------------------------------------------
# The cheat sheet stays a cheat sheet
# ---------------------------------------------------------------------------

def test_the_cheat_sheet_fits_on_a_phone():
    text = _win()._cmd_help()
    assert len(text) <= HELP_MAX_CHARS, (
        f"/help is {len(text)} characters, over the {HELP_MAX_CHARS} ceiling — "
        f"the detail belongs on a /help <command> page")


def test_the_ordering_instructions_are_not_in_the_cheat_sheet():
    """Forty lines on booking lunch pushed everything else off the screen."""
    text = _win()._cmd_help()
    assert "pin:" not in text
    assert "/food order" not in text
    assert "/help food" in text          # but they are one command away
    assert "pin:" in om.FOOD_HELP        # and still written down


def test_the_cheat_sheet_names_every_command():
    text = _win()._cmd_help()
    for cmd in ("/status", "/alarms", "/list", "/plot", "/cancel", "/change",
                "/undo", "/start", "/stop", "/enable", "/disable",
                "/datawatchdog", "/graph", "/window", "/yaxis", "/run",
                "/food"):
        assert cmd in text, f"{cmd} is missing from /help"


def test_one_line_per_command():
    """A bullet that wraps into a paragraph is the thing this rewrite undid."""
    body = _win()._cmd_help()
    long_ones = [ln for ln in body.splitlines()
                 if ln.startswith("- ") and len(ln) > 200]
    assert not long_ones, f"these bullets are paragraphs again: {long_ones}"


def test_the_time_windows_moved_to_their_own_page():
    text = _win()._cmd_help()
    assert "yesterday 7-18" not in text
    assert "yesterday 7-18" in bc.command_help("time")
    assert "/help time" in text or "/help time" in bc.SYNTAX_HELP


def test_tagging_has_a_page_of_its_own_with_the_real_name():
    page = _win()._cmd_help_topic("mention")
    assert "@Diagnostics" in page
    assert page == _win()._cmd_help_topic("tag")


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
