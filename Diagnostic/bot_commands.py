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
# PV name matching
#
# Same rules as every PV search box in the family (Image Slider is the
# reference): words are tokens, AND-matched anywhere in the name, ranked so the
# best hit comes first. A typed fragment that hits several PVs is a FILTER here,
# not an error — "/list plfe" is asking for the PLFE ones, not for a complaint.
# ---------------------------------------------------------------------------

def split_query(text: str) -> list[str]:
    """Query text → lowercase tokens. Spaces, commas, semicolons and '*' all
    separate, so "plfe vrt", "plfe,vrt" and "*plfe**vrt*" are one query: every
    token must sit somewhere in the name (implicit wildcards between them)."""
    return [t for t in re.split(r"[\s,;*]+", (text or "").strip().lower()) if t]


def tokens_in_order(hay: str, tokens: list[str]) -> bool:
    """True when every token occurs in `hay` in the order typed."""
    pos = 0
    for t in tokens:
        i = hay.find(t, pos)
        if i < 0:
            return False
        pos = i + len(t)
    return True


def rank_pv_match(display_name: str, name: str, q) -> Optional[int]:
    """Sort weight of one PV against query `q` (lower = better), or None when it
    does not match at all. `q` may be raw text or a token list.

    Tokens found in the typed order rank above the same tokens scrambled, so a
    wrong guess at the order never hides a PV.
    """
    tokens = q if isinstance(q, (list, tuple)) else split_query(q)
    if not tokens:
        return None
    d, k = (display_name or "").lower(), (name or "").lower()
    field = k.rsplit(":", 1)[-1]
    worst = 0
    total = 0
    for t in tokens:
        if t in (d, k):
            s = 0
        elif field == t:
            s = 1
        elif d.startswith(t) or field.startswith(t) or k.startswith(t):
            s = 2
        elif t in field:
            s = 3
        elif t in d or t in k:
            s = 4
        else:
            return None          # AND semantics: one missing token = no hit
        worst = max(worst, s)
        total += s
    # Weakest token decides the tier; the sum only breaks ties.
    score = worst * 10 + min(total, 9)
    if len(tokens) > 1 and not (tokens_in_order(d, tokens)
                                or tokens_in_order(k, tokens)):
        score += 5
    return score


def search_pvs(pvs, q) -> list:
    """Every PV matching `q`, best first, ties keeping the configured order.

    `pvs` is any sequence of objects carrying ``display_name`` and ``name``;
    the same objects come back.
    """
    tokens = q if isinstance(q, (list, tuple)) else split_query(q)
    if not tokens:
        return []
    scored = []
    for i, p in enumerate(pvs):
        s = rank_pv_match(getattr(p, "display_name", ""),
                          getattr(p, "name", ""), tokens)
        if s is not None:
            scored.append((s, i, p))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [p for _, _, p in scored]


# ---------------------------------------------------------------------------
# Limits — the numbered list behind /change
#
# A PV carries one default ("Global") set of four bounds and, optionally, a
# conditional rule per operating state, each with its own four. Nobody is going
# to type "alarm_high of the 3,3 Hz profile" on a phone, so /change prints the
# lot with a number against each one and takes "6 15" back.
#
# Everything here is duck-typed on the attributes of monitor_tab.PVConfig, the
# way search_pvs is, so the whole grammar tests without Qt.
# ---------------------------------------------------------------------------

# Number-line order — the same order the Thresholds editor shows its columns in,
# so the chat listing and the window read alike.
LIMIT_FIELDS = (
    ("alarm_low", "alarm low"),
    ("warn_low", "warn low"),
    ("warn_high", "warn high"),
    ("alarm_high", "alarm high"),
)

_LIMIT_ATTRS = tuple(a for a, _ in LIMIT_FIELDS)

# Every spelling that means one of the four bounds. The short forms are there
# because the long ones are a lot to type twice on a phone.
_FIELD_WORDS = {
    "alarm_low": ("alarm low", "low alarm", "alarmlow", "lowalarm", "al", "la"),
    "warn_low": ("warn low", "low warn", "warnlow", "lowwarn", "wl", "lw",
                 "warning low", "low warning"),
    "warn_high": ("warn high", "high warn", "warnhigh", "highwarn", "wh", "hw",
                  "warning high", "high warning"),
    "alarm_high": ("alarm high", "high alarm", "alarmhigh", "highalarm",
                   "ah", "ha"),
}

# Words somebody types around the numbers. They carry no meaning for us, but
# refusing the line because of them would be pedantic — "limit 2 hodnota 15" is
# how the question gets answered in this lab, in either language.
_ANSWER_FILLER = {
    "limit", "limity", "hranice", "hranici", "hranicu", "no", "nr", "number",
    "cislo", "číslo", "value", "val", "hodnota", "hodnotu", "na", "to", "set",
    "nastav", "zmen", "změň", "zmeň", "change", "=", "->", "→", ":",
}

# "none" clears a bound: that side then stops raising anything at all.
_ANSWER_NONE = {"none", "off", "zadna", "žádná", "zadne", "žádné", "-", "--",
                "nic", "clear", "empty"}


@dataclass
class LimitRow:
    """One numbered bound in the /change listing."""
    n: int                      # 1-based, as printed
    prof_index: Optional[int]   # None = the Global set, else pv.profiles[i]
    field: str                  # "warn_high", …
    label: str                  # "warn high"
    scope: str                  # "Global" or the rule's label
    value: Optional[float]      # what it is now; None = not set


def profile_label(prof, i: int) -> str:
    """A rule's name for the chat: its own label, else "rule N"."""
    lbl = str((prof or {}).get("label") or "").strip()
    return lbl or f"rule {i + 1}"


def limit_rows(pv) -> list:
    """The numbered bounds of one PV: Global's four, then four per rule.

    The numbering is derived from the configuration, so it is the same on every
    listing until somebody adds or removes a rule — but the rows are handed to
    the caller to keep, rather than recomputed when the answer lands, so an edit
    made in the window meanwhile cannot silently retarget a number.
    """
    rows, n = [], 0
    for attr, label in LIMIT_FIELDS:
        n += 1
        rows.append(LimitRow(n, None, attr, label, "Global",
                             getattr(pv, attr, None)))
    for i, prof in enumerate(getattr(pv, "profiles", None) or []):
        scope = profile_label(prof, i)
        for attr, label in LIMIT_FIELDS:
            n += 1
            rows.append(LimitRow(n, i, attr, label, scope, prof.get(attr)))
    return rows


def parse_limit_field(text: str) -> str:
    """"warn high" / "wh" / "high warning" → "warn_high"; "" when it is none."""
    key = re.sub(r"[\s_]+", " ", (text or "").strip().lower())
    if key in _LIMIT_ATTRS:
        return key
    squashed = key.replace(" ", "")
    for attr, words in _FIELD_WORDS.items():
        if key in words or squashed in words:
            return attr
        if squashed == attr.replace("_", ""):
            return attr
    return ""


def parse_number(text: str) -> Optional[float]:
    """A number as somebody types it, decimal comma included. None if it is not
    one. Kept separate because a rule LABEL may hold a comma too ("3,3 Hz")."""
    t = (text or "").strip().replace(",", ".")
    if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", t):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _match_scope(text: str, rows: list) -> str:
    """The scope (rule label or "Global") named by `text`, or ''. Loose: the
    typed words all have to sit in the label, so "3,3" finds "3,3 Hz"."""
    key = " ".join((text or "").lower().split())
    if not key:
        return ""
    scopes, seen = [], set()
    for r in rows:
        if r.scope not in seen:
            seen.add(r.scope)
            scopes.append(r.scope)
    for s in scopes:                       # exact first
        if s.lower() == key:
            return s
    hits = [s for s in scopes if key in s.lower()]
    if len(hits) == 1:
        return hits[0]
    return ""


def _one_answer(part: str, rows: list) -> tuple:
    """One "<which> <value>" clause → (LimitRow, new value or None)."""
    raw = part.strip()
    if not raw:
        raise CommandError("Say which limit and what number, e.g. `2 15`.")
    # Read the value off the end first: everything before it names the limit.
    # Done this way round because a rule label can itself end in a number
    # ("3,3 Hz" does not, but "rule 2" would).
    words = raw.replace("=", " = ").split()
    words = [w for w in words if w.strip(": ") != "" and w.lower() not in ("=",)]
    if not words:
        raise CommandError(f"I cannot read `{raw}` as a limit and a number.")
    tail = words[-1].strip(".,;:")
    if tail.lower() in _ANSWER_NONE:
        value = None
    else:
        value = parse_number(tail)
        if value is None:
            raise CommandError(
                f"`{raw}` does not end in a number. Say which limit and what "
                f"it should be, e.g. `2 15` — or `2 none` to clear it.")
    head = [w for w in words[:-1]
            if w.strip(".,;:").lower() not in _ANSWER_FILLER]
    if not head:
        raise CommandError(
            f"`{raw}` says what but not which. Put the limit's number first, "
            f"e.g. `2 15`.")
    # A bare number picks a printed row; anything else names a scope and a field.
    if len(head) == 1:
        n = parse_number(head[0])
        if n is not None and float(n).is_integer():
            for r in rows:
                if r.n == int(n):
                    return r, value
            raise CommandError(
                f"There is no limit {int(n)} — the list goes 1 to {len(rows)}.")
    text = " ".join(head)
    field = parse_limit_field(text)
    scope = ""
    if not field:
        # "<scope> <field>": peel the field off the end, the rest is the scope.
        for cut in range(len(head) - 1, 0, -1):
            field = parse_limit_field(" ".join(head[cut:]))
            if field:
                scope = _match_scope(" ".join(head[:cut]), rows)
                if not scope:
                    raise CommandError(
                        f"I do not know a rule called "
                        f"`{' '.join(head[:cut])}` on this PV.")
                break
    if not field:
        raise CommandError(
            f"`{text}` is not one of the limits. Use its number from the list, "
            f"or `warn low`, `warn high`, `alarm low`, `alarm high`.")
    scope = scope or "Global"
    for r in rows:
        if r.field == field and r.scope == scope:
            return r, value
    raise CommandError(f"{scope} has no {field.replace('_', ' ')}.")


def parse_change_answer(text: str, rows: list) -> list:
    """A reply to the /change listing → [(LimitRow, new value or None), …].

    Accepts "6 15", "limit 6 value 15", "limit 6 hodnota 15", "6 = 15",
    "6 15, 7 17", "warn high 25" and "3,3 Hz warn high 14.6". Raises
    CommandError — worded for the chat — on anything it cannot read, which is
    also what keeps ordinary conversation from being taken for an answer.
    """
    raw = (text or "").strip()
    if not raw:
        raise CommandError("Say which limit and what number, e.g. `2 15`.")
    # The whole line as ONE clause first. A rule label may hold a comma
    # ("3,3 Hz"), and splitting on the comma before trying would cut that name
    # in half — several changes at once still work, because a line that really
    # holds two of them cannot be read as one.
    try:
        return [_one_answer(raw, rows)]
    except CommandError as whole_err:
        if ITEM_SEP not in raw:
            raise
        first_err = whole_err
    pairs, seen = [], set()
    for part in raw.split(ITEM_SEP):
        if not part.strip():
            continue
        try:
            row, value = _one_answer(part, rows)
        except CommandError:
            # Neither reading works. The whole-line one is the better message:
            # it is the one that saw the rule name.
            raise first_err
        if row.n in seen:
            raise CommandError(f"Limit {row.n} is in there twice.")
        seen.add(row.n)
        pairs.append((row, value))
    if not pairs:
        raise CommandError("Say which limit and what number, e.g. `2 15`.")
    return pairs


def looks_like_answer(text: str, rows: list) -> bool:
    """True when `text` reads as a reply to a listing — used to decide whether a
    message with no slash in it is meant for us at all."""
    try:
        parse_change_answer(text, rows)
    except CommandError:
        return False
    return True


def check_limit_order(values: dict) -> str:
    """'' when the bounds of one rule are in number-line order, else why not.

    The window's own editor accepts anything, which is how a warning band ends
    up outside its alarm band and nothing ever fires. Typing blind into a chat
    is a worse place to make that mistake, so this one is checked.
    """
    order = [(attr, label, values.get(attr))
             for attr, label in LIMIT_FIELDS]
    have = [(a, lbl, v) for a, lbl, v in order if v is not None]
    for (a1, l1, v1), (a2, l2, v2) in zip(have, have[1:]):
        if v1 > v2:
            return (f"{l1} ({_fmt_num(v1)}) would sit above {l2} "
                    f"({_fmt_num(v2)}) — the four bounds have to run "
                    f"alarm low, warn low, warn high, alarm high, each no "
                    f"higher than the next.")
    return ""


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
    dpi: Optional[int] = None         # resolution of this one picture
    warnings: list = field(default_factory=list)


# Words that ask for the complete reading instead of a fast summary.
_DETAIL_WORDS = ("detail", "details", "full", "everything", "all data", "raw")

# "300dpi", "300 dpi", "dpi 300", "dpi=300" — the resolution of one picture.
_DPI_RE = re.compile(r"^(?:dpi[\s=:]*(\d{2,4})|(\d{2,4})\s*dpi)$", re.I)

# The picture is 8 x 4 inches, so the floor still has to be readable and the
# ceiling still has to be drawable: 1200 dpi is 9600 x 4800 px and ~0.8 s.
DPI_MIN, DPI_MAX = 50, 1200


def parse_dpi_spec(spec: str) -> Optional[int]:
    """Read a '300dpi' option, or None when this segment is not one."""
    m = _DPI_RE.match(" ".join((spec or "").split()))
    if not m:
        return None
    dpi = int(m.group(1) or m.group(2))
    if not (DPI_MIN <= dpi <= DPI_MAX):
        raise CommandError(
            f"{dpi} dpi is outside {DPI_MIN}-{DPI_MAX}. The picture is 8 x 4 "
            f"inches, so 600 dpi is 4800 x 2400 px — anything under "
            f"{DPI_MIN} would be unreadable and over {DPI_MAX} is a print "
            f"file, not a chat message.")
    return dpi


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
        # Before the time parser: a bare number is a window in hours, and
        # "300dpi" must not be read as one.
        dpi = parse_dpi_spec(opt)
        if dpi is not None:
            out.dpi = dpi
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

def bot_tag(bot_name: str = "") -> str:
    """How the bot is tagged in a message — its real name once Webex has told
    us, the example until then."""
    return f"@{bot_name}" if bot_name else "@Diagnostics"


def mention_help(bot_name: str = "") -> str:
    """The first thing anyone needs to know in a group space: tag the bot.

    Two sentences, because this sits at the top of the cheat sheet and the
    cheat sheet has to fit on a phone. The why and the traps are one command
    away, on the `/help mention` page.
    """
    who = bot_tag(bot_name)
    return (
        f"**Tag me first.** In a space with other people in it, Webex only "
        f"shows me the messages that mention me: `{who} /status`. In a "
        f"one-to-one chat with me the tag is not needed."
    )


def mention_help_full(bot_name: str = "") -> str:
    """The long version — `/help mention`.

    Webex only lets a bot READ messages that @mention it when the space has more
    than two people in it — the API returns 403 for anything else. So a command
    typed without the tag is not ignored by us, it never reaches us at all, which
    looks exactly like a broken token from the outside.
    """
    who = bot_tag(bot_name)
    return (
        f"**`{who}` — tagging me**\n"
        f"\n"
        f"In a space with more than two people in it, Webex only shows me the "
        f"messages that mention me by name, so start the line with `{who}`:\n"
        f"- `{who} /status`\n"
        f"- `{who} /plot Chiller 1; yesterday 7-18`\n"
        f"\n"
        f"**Watch out for**\n"
        f"- **Pick the name from the list Webex offers while you type `@`.** A "
        f"typed-out name that is not a real mention does not count — the "
        f"message then never reaches me at all, which looks from the outside "
        f"exactly like a broken bot.\n"
        f"- **In a one-to-one chat with me the tag is not needed**, and a bare "
        f"reply to a question of mine (`/change`) only works there.\n"
        f"- A bare `help`, `?` or `commands` with no slash is taken as `/help`; "
        f"anything else without a slash I stay quiet about, so ordinary "
        f"conversation in the room is not answered."
    )


# Two lines, on purpose. Everything this used to spell out — the catalogue of
# time windows, Y ranges, `; detail`, `; dpi` — now lives on `/help time`,
# because a cheat sheet that has to be scrolled past is not a cheat sheet.
SYNTAX_HELP = (
    "One line, starting with a slash. `,` separates PVs, `;` adds settings: "
    "`/plot Chiller 1, Chiller 2; 7-18; y 15-35` — the windows are on "
    "`/help time`.\n"
    "PV names are matched loosely — any part of the name, any order, case "
    "ignored (`plfe vrt`) — and a fragment that fits several PVs takes them "
    "all. Only `/graph` and `/change` need a single PV."
)
# ---------------------------------------------------------------------------
# `/help <command>` — the long version of one command
#
# The command list in /help has to stay skimmable on a phone, so everything a
# command can do cannot live there. This is where it lives: the syntax, worked
# examples, and — the part a list of options never carries — what to watch out
# for. Kept here rather than in monitor_tab so the wording is testable without
# starting the app.
# ---------------------------------------------------------------------------

_HELP_PLOT = (
    "**`/plot`** — one picture, a curve per PV, over any window.\n"
    "\n"
    "`/plot <pv, pv, …>[; window][; y lo-hi][; detail][; 300dpi]`\n"
    "\n"
    "**Examples**\n"
    "- `/plot Chiller 1` — the last 12 h (whatever **Alert plot window** in "
    "Settings says)\n"
    "- `/plot plfe` — every PLFE PV in one picture; a name fragment takes the "
    "whole group\n"
    "- `/plot Chiller 1, Chiller 2; yesterday 7-18`\n"
    "- `/plot Chiller 1; 1.1. 9:00 - 1.9. 12:00; y 10-30`\n"
    "- `/plot Helium Chiller; 90d; 600dpi`\n"
    "- `/plot all; 24h` — every configured PV\n"
    "\n"
    "**What you get.** Over a short window the curve goes through the readings "
    "themselves. Over a long one each point is the **average** of what it "
    "covers, with a **band from the lowest to the highest** reading behind it, "
    "so a two-second spike three months ago is still drawn at full height. A "
    "single PV also gets its warning/alarm lines; several PVs do not, because "
    "the lines would belong to no visible curve.\n"
    "\n"
    "**Watch out for**\n"
    "- **The band is real but small.** On a chiller over 90 days one point "
    "covers 2.4 h, in which the temperature moves ~0.4 °C — about 1 % of the "
    "picture's height, so it reads as a slightly thick line. The lone vertical "
    "spikes on such a picture are the band's edges, not the average. The "
    "subtitle `line = average, band = lowest…highest` is what tells you the "
    "picture is condensed at all.\n"
    "- **A long window takes minutes.** The reply says the plan "
    "(`180 requests, roughly 2 min`) before it starts and then reports "
    "progress. `/cancel` drops it on the spot; nothing is sent for a cancelled "
    "picture.\n"
    "- **`SAMPLED` means part of the window was not read.** It only happens "
    "past 600 requests: one PV over 90 days is read whole, four chillers over "
    "90 days come back sampled to 83 %, one pressure over a year to 55 %. A "
    "spike falling between two read stretches would not show. `; detail` reads "
    "all of it however long that takes.\n"
    "- **Blank is not zero.** A gap means the archive holds nothing there, or "
    "that stretch could not be read, or it was never asked for — the reply "
    "always says which of the three.\n"
    "- **A bare number is hours, not a resolution.** `; 300` is the last 300 "
    "hours; `; 300dpi` is the resolution.\n"
    "- **One picture per command.** Several PVs share one picture; ask twice "
    "for two pictures.\n"
    "\n"
    "**Resolution.** `; 300dpi` (or `; dpi 300`), "
    f"{DPI_MIN}-{DPI_MAX}. Default is Settings → **Picture resolution (dpi)**, "
    "600, which is 4800 x 2400 px and ~300 kB. Only the pixel count changes — "
    "the text and the lines keep the same proportions.\n"
    "\n"
    "Time windows and Y ranges are the same everywhere — `/help time` has the "
    "list."
)

_HELP_STATUS = (
    "**`/status [pv, pv]`** — the value and state of every PV, or just some.\n"
    "\n"
    "**Examples**\n"
    "- `/status` — all of them, plus a footer saying when they were read and "
    "how the PC running the monitor is doing\n"
    "- `/status plfe` — the four PLFE pressures; the heading says `4 of 31 PVs` "
    "so a part of the list cannot be mistaken for the whole\n"
    "- `/status Chiller 1, Helium Chiller`\n"
    "\n"
    "**Watch out for**\n"
    "- **`[off]` means alerting is off for that PV**, not that it is not being "
    "read. Everything is always read and plotted.\n"
    "- **`not refreshed` in place of `ok`** means this program itself stopped "
    "reading: the numbers are the last ones that landed. A warning goes above "
    "the list as well.\n"
    "- **`not updating`** is about one PV: the archive has nothing newer for "
    "it. A value that simply does not change is never reported — a regulated "
    "chiller sits on one tenth of a degree for two hours.\n"
    "- A name that fits nothing is an error; a name that fits several PVs "
    "takes them all."
)

_HELP_TIME = (
    "**Time windows and Y ranges** — the settings after a `;`. They are the "
    "same wherever they are accepted, which is why they have a page of their "
    "own rather than a place in every command's.\n"
    "\n"
    "**Windows** (all times Europe/Prague)\n"
    "- **A length back from now:** `12h`, `90m`, `2d`, `90d`. A bare number is "
    "hours — `; 300` is the last 300 hours.\n"
    "- **Hours of a day:** `7-18`, `7:30-18:00`. A bare `7-18` means today if "
    "it has already started, otherwise yesterday.\n"
    "- **A named day:** `today`, `yesterday`, `yesterday 7-18`, `15.8. 7-18`, "
    "`2026-08-15`.\n"
    "- **A range between two points in time:** `1.1. 9:00 - 1.9. 12:00`, "
    "`1.1. - 1.9.`, `yesterday 21:00 - now`. A side with no date takes the "
    "other side's date; a side with a date but no time means the whole of that "
    "day.\n"
    "\n"
    "**Y range:** `y 15-35`, or `y auto` to let it scale itself.\n"
    "\n"
    "**`; detail`** reads every single reading instead of a fast summary — "
    "right for a close look, slow over months.\n"
    "\n"
    "**Resolution:** `; 300dpi` (also `; dpi 300`) draws that one picture at "
    f"another resolution, {DPI_MIN}-{DPI_MAX}. The picture is 8 x 4 inches, so "
    "600 dpi is 4800 x 2400 px; the usual figure is set in Settings.\n"
    "\n"
    "**Watch out for**\n"
    "- **A bare number is hours, not a resolution.** `; 300` is 300 hours; "
    "`; 300dpi` is the resolution.\n"
    "- **Two windows in one command**: the last one wins, and the reply says "
    "which it used.\n"
    "- `/window <minutes>` is a different thing altogether — the live graph in "
    "the app's own window."
)

_HELP_CHANGE = (
    "**`/change <pv>`** — move a warning or alarm limit from the chat.\n"
    "\n"
    "`/change DA1` prints the PV's limits with a number against each one; "
    "answer with the number and the new value.\n"
    "\n"
    "```\n"
    "/change DA1          the numbered list\n"
    "6 15                 limit 6 becomes 15\n"
    "```\n"
    "\n"
    "**Ways of saying the same thing**\n"
    "- `6 15`, `limit 6 value 15`, `limit 6 hodnota 15`, `6 = 15`\n"
    "- **Several at once:** `6 15, 7 17`\n"
    "- **By name instead of by number:** `warn high 25` is the Global one, "
    "`3,3 Hz warn high 14.6` that rule's. `alarm low`, `warn low`, "
    "`warn high`, `alarm high` — or `al`, `wl`, `wh`, `ah`\n"
    "- **In one line, without the list:** `/change DA1 6 15` or "
    "`/change DA1 warn high 25`\n"
    "- **Clearing a limit:** `6 none` — that side then raises nothing at all\n"
    "\n"
    "**Watch out for**\n"
    "- **In a room with other people, the answer has to tag me too.** Webex "
    "only delivers messages that mention me, so a bare `6 15` never arrives — "
    "tag me, or send `/change 6 15`, which is the same thing. In a one-to-one "
    "chat with me the bare reply works.\n"
    "- **One PV at a time.** A fragment that fits several PVs is answered with "
    "the candidates to pick from, unlike `/status` or `/plot`, where a "
    "fragment takes the whole group. There is one list of limits to number.\n"
    "- **A PV with conditional rules has more than four limits.** A chiller "
    "with a rule per shot rate has the Global four plus four per rule, and "
    "the listing marks the one in force right now. Changing the Global set of "
    "such a PV changes what is used only when no rule matches.\n"
    "- **It takes effect at the next reading**, not instantly — and alerting "
    "for that PV is then held for the rule-change wait (Settings → "
    "**Hold after a limit change**, 20 min) while the value follows, exactly "
    "as when a rule takes over by itself. A value that still misses the new "
    "band when the wait is over alerts then.\n"
    "- **The order is checked**, which the window's own editor does not do: "
    "alarm low, warn low, warn high, alarm high have to run lowest to "
    "highest. A value that would cross another is refused and nothing is "
    "written.\n"
    "- **The change is saved and published** to the shared PV list, so it "
    "survives a restart and reaches the other copies. If the share cannot be "
    "reached the reply says the change is on that one PC only.\n"
    "- **I forget the question after 15 minutes**, and `/cancel` drops it. "
    "`/undo` puts the last change back.\n"
    "- **Only while the app is open.** Unlike `/run` and `/food`, this one is "
    "not answered by the always-on listener — it has no PV list to change."
)

_HELP_UNDO = (
    "**`/undo`** — put the last limit change back.\n"
    "\n"
    "One step, and only for `/change`: the values as they were before the most "
    "recent change, whoever made it. The reply names them.\n"
    "\n"
    "**Watch out for**\n"
    "- **One step only.** Two changes and one `/undo` leaves the first one "
    "standing; `/change` it back by hand.\n"
    "- **It is forgotten when the app restarts**, and it does not reach back "
    "past an edit made in the window itself.\n"
    "- Undoing is a change like any other, so the same wait before alerting "
    "resumes applies."
)

_HELP_ALARMS = (
    "**`/alarms [pv, pv]`** — only what is in warning or alarm right now, plus "
    "any PV whose readings stopped arriving.\n"
    "\n"
    "**Examples**\n"
    "- `/alarms` — everything\n"
    "- `/alarms chiller` — only that group, and the heading says so\n"
    "\n"
    "**Watch out for**\n"
    "- **It refuses to say \"all clear\" on out-of-date values.** If the "
    "program has stopped reading it says it cannot tell, and what was true at "
    "the last reading.\n"
    "- **Alerting off for the whole group** is answered as such, not as "
    "\"no alarms\" — a PV nobody is judging cannot be called fine.\n"
    "- A PV that is not live is listed as the data fault it is, not as whatever "
    "its old reading scores against the limits."
)

_HELP_LIST = (
    "**`/list [pv, pv]`** — the configured PVs.\n"
    "\n"
    "**Examples**\n"
    "- `/list` — all of them\n"
    "- `/list plfe` — `PVs matching 'plfe' (4 of 31)`\n"
    "- `/list vrt, chiller` — both groups in one answer\n"
    "\n"
    "Words are matched anywhere in the name shown and in the archiver name, in "
    "any order, and all of them have to appear (`plfe vrt`). Only a fragment "
    "that fits nothing is an error."
)

_HELP_GRAPH = (
    "**`/graph <pv|all>`** — what the live graph in the app's own window "
    "shows. It changes the window on the PC, not a picture in the chat; "
    "`/plot` is the one that sends a picture.\n"
    "\n"
    "**Watch out for:** this one takes **one** PV or `all`. A fragment that "
    "fits several PVs is answered with the candidates to choose from, because "
    "the live graph has one curve at a time. `/window <minutes>` and "
    "`/yaxis <lo-hi>|auto` belong to the same graph."
)

_HELP_STOP = (
    "**`/stop [hours]`** / **`/start`** — alerting off and on.\n"
    "\n"
    "**Examples**\n"
    "- `/stop` — off until somebody sends `/start`\n"
    "- `/stop 10` — off for ten hours, then back on by itself (the reply says "
    "at what time)\n"
    "\n"
    "**Watch out for**\n"
    "- **PVs are still read and plotted while alerting is off.** `/status` and "
    "`/plot` answer exactly as before; only the notifications stop.\n"
    "- **`/stop` does not stop a running plot.** That is `/cancel` — if a plot "
    "is still being fetched the reply says so and offers it.\n"
    "- Turning it off silences every PV. For one PV use `/disable <pv>`."
)

_HELP_ENABLE = (
    "**`/enable <pv, pv>`** / **`/disable <pv, pv>`** — alerting for "
    "individual PVs.\n"
    "\n"
    "**Examples**\n"
    "- `/disable plfe` — all four PLFE pressures at once; the reply names every "
    "PV it changed\n"
    "- `/enable Chiller 1, Chiller 2`\n"
    "\n"
    "**Watch out for:** a name fragment takes the whole group, so check the "
    "names in the reply. A disabled PV is still read, still plotted and still "
    "shown by `/status` — it simply cannot raise an alert. The change is saved "
    "and survives a restart."
)

_HELP_CANCEL = (
    "**`/cancel`** (also `/abort`, `/nevermind`) — drop a plot that is still "
    "being fetched.\n"
    "\n"
    "A year-long window is hundreds of requests to the archive and minutes of "
    "waiting; this stops the requests that have not gone out yet, so the right "
    "window can be asked for straight away. **Nothing at all is sent for a "
    "cancelled plot.**\n"
    "\n"
    "It also drops an unanswered `/change` question, so a limit listing you no "
    "longer want cannot be answered by accident.\n"
    "\n"
    "**Watch out for:** requests already in flight still have to come back, so "
    "the reply is not instant. \"Nothing is running\" means there was nothing "
    "to take back."
)

_HELP_WINDOW = (
    "**`/window <minutes>`** — the time span of the live graph in the app's own "
    "window (not of a `/plot` picture; that one takes its window after a `;`).\n"
    "\n"
    "Widening it past what is already in memory makes the program fetch the "
    "missing stretch from the archive, which takes a moment."
)

_HELP_YAXIS = (
    "**`/yaxis <lo-hi>`** or **`/yaxis auto`** — the Y range of the live graph "
    "in the app's own window.\n"
    "\n"
    "Examples: `/yaxis 10-30`, `/yaxis 10 30`, `/yaxis auto`. For a picture in "
    "the chat the same thing is an option: `/plot Chiller 1; y 10-30`."
)

_HELP_WATCHDOG = (
    "**`/datawatchdog on|off`** — the \"no data at all\" alert: one message "
    "when every PV stops answering (an archiver or network outage) and one more "
    "when data comes back. With no argument it says the current state.\n"
    "\n"
    "**Watch out for:** this is the all-PVs-at-once alert. A single PV falling "
    "silent is that PV's own business (`no data`, `not updating`), and the "
    "program stopping reading altogether is the `not refreshed` warning."
)

_HELP_RUN = (
    "**`/run`** — start the program on the PC when it has been closed.\n"
    "\n"
    "It is answered by the always-on listener, which is a separate little "
    "program. If the app itself is already up, it answers `already running and "
    "tracking` so that a silent room never means \"unknown command\"."
)

COMMAND_HELP = {
    "plot": _HELP_PLOT,
    "time": _HELP_TIME,
    "status": _HELP_STATUS,
    "alarms": _HELP_ALARMS,
    "list": _HELP_LIST,
    "change": _HELP_CHANGE,
    "undo": _HELP_UNDO,
    "graph": _HELP_GRAPH,
    "stop": _HELP_STOP,
    "enable": _HELP_ENABLE,
    "cancel": _HELP_CANCEL,
    "window": _HELP_WINDOW,
    "yaxis": _HELP_YAXIS,
    "datawatchdog": _HELP_WATCHDOG,
    "run": _HELP_RUN,
}

# `mention` is not in COMMAND_HELP because its page has to carry the bot's real
# name, so it is built by mention_help_full() rather than being a constant. It
# is still a topic /help answers, which is what this is for.
MENTION_WORDS = ("mention", "mentions", "tag", "tagging", "@")


def is_mention_topic(name: str) -> bool:
    """True when `/help <name>` is asking about tagging the bot."""
    key = (name or "").strip().lower().lstrip("/").rstrip("?,.;:")
    key = key.split()[0] if key.split() else ""
    return key in MENTION_WORDS

# Words that mean the same topic. /food is not here: its own long help lives in
# okbase_menu.FOOD_HELP and monitor_tab hands that over, so the two can never
# drift apart.
_HELP_ALIASES = {
    "abort": "cancel", "nevermind": "cancel",
    "start": "stop",
    "disable": "enable",
    "alarm": "alarms", "alerts": "alarms", "warnings": "alarms",
    "pvs": "list", "pv": "list",
    "y": "yaxis", "yrange": "yaxis",
    "rundiagnostic": "run",
    "watchdog": "datawatchdog", "data": "datawatchdog",
    "dpi": "plot", "detail": "plot", "sampled": "plot", "graphs": "plot",
    "picture": "plot", "resolution": "plot",
    "times": "time", "range": "time", "ranges": "time", "windows": "time",
    "when": "time", "dates": "time", "date": "time",
    "limit": "change", "limits": "change", "threshold": "change",
    "thresholds": "change", "setlimit": "change", "set": "change",
    "undochange": "undo", "revert": "undo", "back": "undo",
}


def help_topic(name: str) -> str:
    """Canonical topic for whatever the operator typed, or '' if there is none."""
    key = (name or "").strip().lower().lstrip("/")
    key = key.split()[0] if key.split() else ""
    key = key.rstrip("?,.;:")
    if key in COMMAND_HELP:
        return key
    return _HELP_ALIASES.get(key, "")


def command_help(name: str) -> str:
    """The long help for one command, or '' when the topic is unknown."""
    return COMMAND_HELP.get(help_topic(name), "")


def help_topics() -> list:
    """Every topic worth naming in a \"try one of these\" line."""
    return sorted(COMMAND_HELP)
