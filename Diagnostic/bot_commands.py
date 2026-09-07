"""Command-language parsing for the PV Monitor Webex bot.

Deliberately Qt-free and network-free, so the whole grammar can be unit-tested
on its own (``test_bot_commands.py``) without starting the app.

Grammar
-------
    /command  item, item, item ;  option ;  option

* ``,`` separates items — usually PV names:
      ``/plot Chiller 1, Chiller 2, Chiller 3``
* ``;`` separates options that configure the command — a time window, a fixed
  Y range:
      ``/plot Chiller 1, Chiller 2; 7-18; y 15-35``

Everything after the first ``;`` is an option, so a PV name may safely contain
spaces; only ``,`` and ``;`` are structural.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

ITEM_SEP = ","
OPT_SEP = ";"

TZ_DEFAULT = ZoneInfo("Europe/Prague")
NS = 1_000_000_000


class CommandError(ValueError):
    """Bad command text, with a message written to be shown in the chat."""


# ---------------------------------------------------------------------------
# Command splitting
# ---------------------------------------------------------------------------

@dataclass
class ParsedCommand:
    cmd: str              # "/plot" (lower-cased)
    items: list[str]      # comma-separated first segment, blanks dropped
    options: list[str]    # every segment after the first ';', blanks dropped
    args: str             # everything after the command word, unsplit

    @property
    def first(self) -> str:
        return self.items[0] if self.items else ""

    @property
    def joined(self) -> str:
        return ", ".join(self.items)


def parse_command(text: str) -> ParsedCommand:
    """Split one chat line into command / items / options."""
    text = (text or "").strip()
    if not text:
        return ParsedCommand("", [], [], "")
    parts = re.split(r"\s+", text, maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    # An option written straight onto the command — "/food; cz" or "/food;cz" —
    # is the natural way to type it, and it used to make the command itself
    # "/food;", which matched nothing and answered "unknown command".
    if OPT_SEP in cmd:
        cmd, _, glued = cmd.partition(OPT_SEP)
        args = f"{OPT_SEP}{glued} {args}".strip()
    segments = [s.strip() for s in args.split(OPT_SEP)]
    items = [i.strip() for i in segments[0].split(ITEM_SEP) if i.strip()]
    options = [s for s in segments[1:] if s]
    return ParsedCommand(cmd, items, options, args)


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------

@dataclass
class TimeRange:
    start_ns: int
    end_ns: int
    label: str            # human-readable, goes into the reply and plot title

    @property
    def hours(self) -> float:
        return (self.end_ns - self.start_ns) / (3600 * NS)


# "12", "12h", "1.5 h", "90m", "2d" — a window ending now.
_REL_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(h|hod|hours?|m|min|mins?|d|days?)?$", re.I)
# "7-18", "7:30-18:00", "22-6" (crosses midnight)
_CLOCK_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*-\s*(\d{1,2})(?::(\d{2}))?$")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_CZ_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})?$")


def _fmt_num(n: float) -> str:
    return f"{n:g}"


def _parse_date(token: str, now: datetime):
    """Return a date for '2026-08-15', '15.8.' or '15.8.2026', else None.

    A bare day.month with no year means the most recent such date: this year
    normally, last year if that would be in the future.
    """
    m = _ISO_DATE_RE.match(token)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        return datetime(y, mo, d).date()
    m = _CZ_DATE_RE.match(token)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else now.year
        day = datetime(y, mo, d).date()
        if not m.group(3) and day > now.date():
            day = datetime(y - 1, mo, d).date()
        return day
    return None


def _fmt_range(start: datetime, end: datetime, truncated: bool,
               day_end: bool = False) -> str:
    if day_end:
        # The end was typed as a bare date, so say that date rather than the
        # midnight that follows it — nobody types "1.9." and expects to read
        # "2. 9. 00:00" back.
        last = end - timedelta(seconds=1)
        return (f"{start:%Y-%m-%d %H:%M}–{last:%Y-%m-%d} "
                f"(to the end of the day)")
    if start.date() == end.date():
        label = f"{start:%Y-%m-%d %H:%M}–{end:%H:%M}"
    else:
        label = f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M}"
    return label + (" (so far)" if truncated else "")


# --- a range between two points in time ------------------------------------
#
# "1.1. 9:00 - 1.9. 12:00". Tried only after the single-window parser below has
# refused the text, so none of the older forms can be captured by accident.

_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?$")
_DASHES = ("-", "–", "—")     # hyphen, en dash, em dash
MAX_RANGE_DAYS = 3 * 366


class _Endpoint:
    """One side of a range: a day, a time of day, either, or the word 'now'."""

    def __init__(self, day=None, tm=None, is_now=False, year_guessed=False):
        self.day = day
        self.tm = tm
        self.is_now = is_now
        # True when the user wrote "5.9." and the year was filled in for them.
        self.year_guessed = year_guessed

    @property
    def has_date(self) -> bool:
        return self.day is not None

    @property
    def whole_day(self) -> bool:
        return self.day is not None and self.tm is None


def _parse_endpoint(text: str, now: datetime):
    """'1.1. 9:00' | '2026-01-01' | '9:00' | 'today 7' | 'now' -> _Endpoint."""
    tokens = text.split()
    if not tokens:
        return None
    if tokens == ["now"]:
        return _Endpoint(is_now=True)

    day = None
    year_guessed = False
    if tokens[0] == "today":
        day, tokens = now.date(), tokens[1:]
    elif tokens[0] == "yesterday":
        day, tokens = (now - timedelta(days=1)).date(), tokens[1:]
    else:
        parsed = _parse_date(tokens[0], now)
        if parsed is not None:
            m = _CZ_DATE_RE.match(tokens[0])
            year_guessed = bool(m and not m.group(3))
            day, tokens = parsed, tokens[1:]
    if tokens and tokens[0] in ("time", "t", "from", "at"):
        tokens = tokens[1:]

    tm = None
    if tokens:
        if len(tokens) > 1:
            return None
        m = _TIME_RE.match(tokens[0])
        if m is None:
            return None
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        if h > 24 or mi > 59 or (h == 24 and mi):
            return None
        tm = dtime(0, 0) if h == 24 else dtime(h, mi)
        if h == 24:
            # "1.9. 24:00" means the end of that day.
            day = day + timedelta(days=1) if day is not None else None
    if day is None and tm is None:
        return None
    return _Endpoint(day, tm, year_guessed=year_guessed)


def _parse_range(spec: str, now: datetime, tz):
    """Return (start, end, day_end) for a two-point range, else None."""
    for i, ch in enumerate(spec):
        if ch not in _DASHES:
            continue
        left, right = spec[:i].strip(), spec[i + 1:].strip()
        if not left or not right:
            continue
        a = _parse_endpoint(left, now)
        b = _parse_endpoint(right, now)
        if a is None or b is None:
            continue
        # Without a date on at least one side this is an ordinary clock window
        # ("7-18"), which the parser above already understands.
        if not (a.has_date or b.has_date):
            continue

        start_day = a.day or b.day
        start = datetime.combine(start_day, a.tm or dtime(0, 0), tzinfo=tz)
        if b.is_now:
            return start, now, False
        end_day = b.day or a.day
        if b.year_guessed and end_day < start_day:
            # "1.9. - 5.9." typed on 2 September. A lone date means the most
            # recent one, which for the far end of a range would land a year
            # before the near end. The user plainly meant the days that follow.
            try:
                end_day = end_day.replace(year=end_day.year + 1)
            except ValueError:                    # 29 February
                end_day = end_day.replace(year=end_day.year + 1, day=28)
        if b.whole_day:
            # A bare date on the right means the whole of that day.
            end = datetime.combine(end_day + timedelta(days=1), dtime(0, 0),
                                   tzinfo=tz)
            return start, end, True
        return start, datetime.combine(end_day, b.tm, tzinfo=tz), False
    return None


def parse_time_spec(spec: str, now_ns: int, tz=TZ_DEFAULT) -> TimeRange:
    """Turn a time option into an absolute range. Raises CommandError if unclear.

    Accepted forms:
        12h | 90m | 2d | 12          last N hours/minutes/days, ending now
        7-18 | 7:30-18:00            that clock window; today if it has already
                                     started, otherwise yesterday's
        today | yesterday            that whole day (today ends now)
        today 7-18 | yesterday 7-18  clock window on that day
        2026-08-15 | 15.8. | 15.8.2026        that whole day
        2026-08-15 7-18 | 15.8. 7-18          clock window on that date
        1.1. 9:00 - 1.9. 12:00       a range between two points in time
        1.1. - 1.9.                  ... whole days when a side has no time
        yesterday 21:00 - now        ... 'now' is allowed on the right
    """
    try:
        return _parse_single_window(spec, now_ns, tz)
    except CommandError as first:
        # Only text the older forms cannot explain is offered to the range
        # parser, and when that fails too the first message is the better one.
        s = " ".join((spec or "").split()).lower()
        now = datetime.fromtimestamp(now_ns / NS, tz=tz)
        got = _parse_range(s, now, tz) if s else None
        if got is None:
            raise first
        start, end, day_end = got
        if end <= start:
            raise CommandError(
                f"The range '{spec}' ends before it starts.") from None
        if (end - start).days > MAX_RANGE_DAYS:
            raise CommandError(
                f"'{spec}' is longer than three years — that is more than the "
                f"archive can be asked for in one go.") from None
        truncated = False
        if end > now:
            end, truncated, day_end = now, True, False
        if end <= start:
            raise CommandError(
                f"The range '{spec}' is entirely in the future.") from None
        return TimeRange(int(start.timestamp() * NS), int(end.timestamp() * NS),
                         _fmt_range(start, end, truncated, day_end))


def _parse_single_window(spec: str, now_ns: int, tz=TZ_DEFAULT) -> TimeRange:
    """The one-window forms: a relative span, a clock window, a whole day."""
    s = " ".join((spec or "").split()).lower()
    if not s:
        raise CommandError("Empty time window.")
    now = datetime.fromtimestamp(now_ns / NS, tz=tz)

    m = _REL_RE.match(s)
    if m:
        n = float(m.group(1))
        if n <= 0:
            raise CommandError("The time window must be longer than zero.")
        unit = (m.group(2) or "h")[0]
        seconds = n * {"h": 3600.0, "m": 60.0, "d": 86400.0}[unit]
        if seconds > MAX_RANGE_DAYS * 86400.0:
            raise CommandError(
                f"'{spec}' is longer than three years — that is more than the "
                f"archive can be asked for in one go.")
        return TimeRange(now_ns - int(seconds * NS), now_ns,
                         f"last {_fmt_num(n)} {unit}")

    tokens = s.split()
    day = None
    if tokens[0] == "today":
        day, tokens = now.date(), tokens[1:]
    elif tokens[0] == "yesterday":
        day, tokens = (now - timedelta(days=1)).date(), tokens[1:]
    else:
        parsed = _parse_date(tokens[0], now)
        if parsed is not None:
            day, tokens = parsed, tokens[1:]
    # Optional "time"/"t" marker before the clock window ("; t 7-18").
    if tokens and tokens[0] in ("time", "t", "from"):
        tokens = tokens[1:]
    rest = " ".join(tokens)

    if not rest:
        if day is None:
            raise CommandError(f"I don't understand the time window '{spec}'.")
        start = datetime.combine(day, dtime(0, 0), tzinfo=tz)
        end = start + timedelta(days=1)
    else:
        cm = _CLOCK_RE.match(rest)
        if cm is None:
            raise CommandError(f"I don't understand the time window '{spec}'.")
        h1, m1 = int(cm.group(1)), int(cm.group(2) or 0)
        h2, m2 = int(cm.group(3)), int(cm.group(4) or 0)
        if h1 > 23 or h2 > 24 or m1 > 59 or m2 > 59:
            raise CommandError(f"'{rest}' is not a valid clock window (use 0-24 h).")
        base = day if day is not None else now.date()
        midnight = datetime.combine(base, dtime(0, 0), tzinfo=tz)
        start = midnight + timedelta(hours=h1, minutes=m1)
        end = midnight + timedelta(hours=h2, minutes=m2)
        if end <= start:
            end += timedelta(days=1)      # window crosses midnight (22-6)
        if day is None and start >= now:
            # No date given and today's window has not started yet — the most
            # recent one the archive can actually answer is yesterday's.
            start -= timedelta(days=1)
            end -= timedelta(days=1)

    truncated = False
    if end > now:
        end, truncated = now, True        # a window running into the future
    if end <= start:
        raise CommandError(f"The window '{spec}' is entirely in the future.")
    return TimeRange(int(start.timestamp() * NS), int(end.timestamp() * NS),
                     _fmt_range(start, end, truncated))


# ---------------------------------------------------------------------------
# Plot options
# ---------------------------------------------------------------------------

_Y_RE = re.compile(r"^y(?:axis)?\b[\s=:]*(.*)$", re.I)
_YRANGE_RE = re.compile(
    r"^(-?\d+(?:\.\d+)?)\s*(?:\.\.|-|to|\s)\s*(-?\d+(?:\.\d+)?)$", re.I)


def parse_yaxis_spec(spec: str):
    """Return (lo, hi) for 'y 10-30', None for 'y auto'.

    Raises CommandError when the option starts with y but is not usable.
    """
    body = (spec or "").strip()
    m = _Y_RE.match(body)
    if m is None:
        raise CommandError(f"'{spec}' is not a Y-axis option.")
    body = m.group(1).strip()
    if body in ("", "auto"):
        return None
    r = _YRANGE_RE.match(body)
    if r is None:
        raise CommandError(f"I don't understand the Y range '{body}' "
                           "— use `y 10-30` or `y auto`.")
    lo, hi = float(r.group(1)), float(r.group(2))
    if hi <= lo:
        raise CommandError(f"Y range '{body}' must go low to high.")
    return lo, hi


@dataclass
class PlotOptions:
    time: Optional[TimeRange] = None
    yaxis: Optional[tuple] = None     # (lo, hi); None = autoscale
    detail: bool = False              # read every reading, however long it takes
    warnings: list = field(default_factory=list)


# Words that ask for the complete reading instead of a fast summary.
_DETAIL_WORDS = ("detail", "details", "full", "everything", "all data", "raw")


def parse_plot_options(options: list[str], now_ns: int,
                       tz=TZ_DEFAULT) -> PlotOptions:
    """Interpret the ';' segments of a plot command. Raises CommandError with
    a chat-ready message on anything it cannot place."""
    out = PlotOptions()
    for opt in options:
        low = " ".join(opt.split()).lower()
        if low in _DETAIL_WORDS:
            out.detail = True
            continue
        if _Y_RE.match(opt):
            out.yaxis = parse_yaxis_spec(opt)
            continue
        if out.time is not None:
            # Two time windows in one command used to let the last one win in
            # silence. With ranges in the language that is easy to do by
            # accident, so say which one is being used.
            out.warnings.append(
                f"Two time windows were given; I am using the last one, "
                f"'{opt.strip()}'.")
        out.time = parse_time_spec(opt, now_ns, tz)
    return out


# ---------------------------------------------------------------------------
# The syntax half of /help (the command list lives with the commands)
# ---------------------------------------------------------------------------

def mention_help(bot_name: str = "") -> str:
    """The first thing anyone needs to know in a group space: tag the bot.

    Webex only lets a bot READ messages that @mention it when the space has more
    than two people in it — the API returns 403 for anything else. So a command
    typed without the tag is not ignored by us, it never reaches us at all, which
    looks exactly like a broken token from the outside. `bot_name` is the bot's
    real name once the app has asked Webex for it; the example stands in until then.
    """
    who = f"@{bot_name}" if bot_name else "@Diagnostics"
    return (
        f"**Tag me first.** In a space with other people in it, Webex only shows "
        f"me the messages that mention me by name, so start the line with "
        f"`{who}`:\n"
        f"- `{who} /status`\n"
        f"- `{who} /plot Chiller 1; yesterday 7-18`\n"
        f"\n"
        f"Pick the name from the list Webex offers while you type `@` — a typed-out "
        f"name that is not a real mention does not count. In a one-to-one chat with "
        f"me the tag is not needed."
    )


SYNTAX_HELP = (
    "**How to talk to me** — one line, starting with a slash. Two separators:\n"
    "- `,` separates items, so several PVs go in one command: "
    "`/plot Chiller 1, Chiller 2, Chiller 3`\n"
    "- `;` adds settings after the items: "
    "`/plot Chiller 1, Chiller 2; 7-18; y 15-35`\n"
    "\n"
    "PV names are matched loosely — any unique part of the name is enough, "
    "case does not matter.\n"
    "\n"
    "**Time windows** (after a `;`, all times Europe/Prague): `12h`, `90m`, "
    "`2d`, `7-18`, `7:30-18:00`, `today`, `yesterday`, `yesterday 7-18`, "
    "`15.8. 7-18`, `2026-08-15`. A bare `7-18` means today if it has already "
    "started, otherwise yesterday.\n"
    "**A range between two points in time:** `1.1. 9:00 - 1.9. 12:00`, "
    "`1.1. - 1.9.`, `yesterday 21:00 - now`. A side with no date takes the "
    "other side's date; a side with a date but no time means the whole of that "
    "day.\n"
    "**Y range:** `y 15-35` or `y auto`.\n"
    "**`; detail`** reads every single reading instead of a fast summary — "
    "right for a close look, slow over months."
)
