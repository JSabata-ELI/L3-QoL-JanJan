"""Canteen menu from the OKbase portal, for the `/food` chat command.

Deliberately Qt-free and importable on its own, like ``bot_commands.py`` and
``memstats.py``: the whole thing can be exercised from a shell
(``python okbase_menu.py --week``) and unit-tested offline against a saved
payload (``test_okbase_menu.py``).

Nothing here raises. Every function that touches the network or the disk
returns a value plus an error string, the same discipline the notifiers follow
(``alerting.WebexNotifier.send``): a broken portal must never take the bot down.

Why a cache file
----------------
The menu changes at most once a day, so the bot does not need a live OKbase
session to answer `/food`. Only the *refresh* needs credentials, and those are
DPAPI blobs that decrypt for one Windows account on one PC. So:

    Diagnostic (the PC whose credentials work)
        |  refresh once a day, and on demand
        v
    menu_cache.json  ->  /food in Diagnostic
                     ->  /food in the standalone Webex listener (always on)

Both command handlers only ever READ the cache, which is why `/food` still
answers when the app is closed. When the cache cannot answer for the day asked
about, the reply says so — it never passes old food off as today's.

The portal
----------
`https://elieric.okbase.cz/okbase/web-client/web/objednavky-jidel` is an Angular
front end over a JSON REST API, so this is a plain API client — there is no HTML
parsing anywhere in here.

    POST <base>/rest/stravovani/objednavky/nacti-vse    the meal list
    GET  <base>/rest/app-info/serverovy-cas             401 unless signed in
    POST <base>/rest/authentication/manual              username + password
    GET  <base>/rest/authentication/web-login-config    which sign-ins are on

The request body of ``nacti-vse`` is not documented and the front end's own type
is erased at runtime, so FILTER_CANDIDATES holds the plausible shapes and the
first one the server accepts is remembered in the cache. The response field
names are read the same tolerant way (see ``parse_menu``) — this is a vendor
product and an upgrade may rename things; a rename must cost a menu, not the bot.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Optional

import shared_pvs

BASE_DEFAULT = "https://elieric.okbase.cz/okbase/service"
# The page a PERSON opens. Not the REST base: the browser sign-in window has to
# land somewhere recognisable, and this is the page they would open anyway. Kept
# here with the rest of the portal's addresses so there is one owner of them.
MENU_PAGE_DEFAULT = ("https://elieric.okbase.cz/okbase/web-client/web/"
                     "objednavky-jidel")
CACHE_FILENAME = "menu_cache.json"
TIMEOUT_DEFAULT = 20.0

# Sent as x-okbase-* headers on every call, exactly as the portal's own front
# end does. One OKbase server can host several organisations and data sources,
# so these are part of the question, not decoration — see _session().
DATASOURCE_DEFAULT = "defaultDataSource"
LANGUAGE_DEFAULT = "cs"
ORG_ID_DEFAULT = "1"

# A cache older than this is called out in the reply even when it does cover the
# day asked about: one working day plus the night, so a Monday morning question
# answered from Friday's refresh is flagged.
STALE_AFTER_HOURS = 30.0

# How far a refresh reaches, measured from the Monday of this week. These are
# the browser's own numbers (captured 2026-08-26: it asked for 2026-07-27 to
# 2026-09-06 while displaying the week of the 24th), and matching them is
# deliberate — asking a narrower range than the front end ever asks risks
# getting only the one displayed week back.
FETCH_DAYS_BACK = 28
FETCH_DAYS_AHEAD = 13

# How many weeks a refresh collects, starting with the current one. The portal
# returns ONE week per request (the weekStart/weekEnd one), so this is a count of
# requests: this week, so /food works, and the next, so "/food next week" does.
FETCH_WEEKS = 2

# How often a borrowed browser session is touched so it does not idle out.
# A web session dies of being UNUSED, so one cheap request on a clock keeps it
# alive indefinitely; without this the paste would only be good for one
# server-side timeout (typically half an hour) and would have to be redone
# every day. Comfortably inside the shortest timeout worth expecting.
KEEPALIVE_MINUTES_DEFAULT = 10

DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")


# ---------------------------------------------------------------------------
# Meals
# ---------------------------------------------------------------------------

CZECH_LETTERS = set("ěščřžýáíéúůďťňóĚŠČŘŽÝÁÍÉÚŮĎŤŇÓ")


def _has_czech(text: str) -> bool:
    return any(ch in CZECH_LETTERS for ch in text)


def split_languages(name: str) -> tuple[str, str]:
    """(Czech, English) out of one portal name, or (whole name, "").

    The canteen writes both languages into a single field separated by a slash,
    but not always, not in a fixed order, and sometimes only one of them:

        "Kuřecí vývar/Chicken broth"        both, no spaces
        "Svíčková na smetaně / Beef sirloin"  both, spaced
        "Letní salátová miska :)"            Czech only
        "Máslové fazole s ... a řecká jogurt" Czech only, and it has a slash-free
                                              comma list that must not be touched

    Which half is which is decided by Czech diacritics rather than by position,
    because the order is not reliable. When BOTH halves look Czech (a name like
    "vepřové/kuřecí" is one dish, not two languages) or neither does, the name is
    left whole — showing it unsplit is always safe, mangling it is not.
    """
    text = (name or "").strip()
    parts = [p.strip() for p in re.split(r"\s*/\s*", text) if p.strip()]
    if len(parts) != 2:
        return text, ""
    first, second = parts
    if _has_czech(first) and not _has_czech(second):
        return first, second
    if _has_czech(second) and not _has_czech(first):
        return second, first
    return text, ""


@dataclass
class Meal:
    name: str
    price: str = ""          # already formatted for display ("95 Kč")
    kind: str = ""           # soup / main course / … as the portal labels it

    def as_dict(self) -> dict:
        return {"name": self.name, "price": self.price, "kind": self.kind}

    @staticmethod
    def from_dict(d: dict) -> "Meal":
        return Meal(name=str(d.get("name") or ""),
                    price=str(d.get("price") or ""),
                    kind=str(d.get("kind") or ""))

    def names(self) -> tuple[str, str]:
        return split_languages(self.name)

    def titles(self, lang: str) -> tuple[str, str]:
        """(main line, second line) for the wanted language.

        A one-language meal never disappears because the other language was
        asked for — half the menu has no English at all, and a blank line where
        lunch should be is worse than the wrong language.
        """
        czech, english = self.names()
        if lang == "cs":
            return (czech or english), ""
        if lang == "en":
            return (english or czech), ""
        return czech, english


# ---------------------------------------------------------------------------
# Reading the portal's JSON without knowing its exact schema
# ---------------------------------------------------------------------------

# Confirmed against the real answer (2026-08-26): a meal is
# listky[<date>].polozky[].jidlo = {nazev, typ, aktualniCenaDotovana,
# aktualniCenaPlna, popis, kategorie[], …}. `popis` is deliberately NOT a name
# key: here it holds the allergen numbers ("9"), so a meal with no `nazev` would
# otherwise be listed as "9".
_NAME_KEYS = ("nazev", "nazevjidla", "jidlonazev", "nazevcz",
              "name", "title", "text")
# Subsidised price first — that is what the person actually pays.
_PRICE_KEYS = ("aktualnicenadotovana", "aktualnicenaplna", "cenadotovana",
               "cena", "cenajidla", "cenacelkem", "castka", "price", "amount")
_DATE_KEYS = ("datum", "datumjidla", "datumvydeje", "denvydeje", "den",
              "date", "day")
_KIND_KEYS = ("jidlotypnazev", "jidlotyp", "typjidla", "nazevtypu", "typ",
              "druh", "kategorie", "kind", "type", "chod")
# Keys whose values are never a meal name, however name-like they look.
_SKIP_KEYS = ("jidelna", "uzivatel", "user", "canteen", "stav", "state")
# Sub-objects that are a value of the meal, never a container of more meals.
# A code-book object like ``"jidloTyp": {"nazev": "soup"}`` carries a name key,
# so walking into it would invent a meal called "soup" on the same day.
_NO_RECURSE = _SKIP_KEYS + _KIND_KEYS + _PRICE_KEYS + _DATE_KEYS


def _norm(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def _pick(d: dict, keys: tuple[str, ...]) -> Any:
    """First value in `d` whose key matches one of `keys` (case/underscore-blind)."""
    lowered = {_norm(k): v for k, v in d.items()}
    for want in keys:
        val = lowered.get(want)
        if val not in (None, "", [], {}):
            return val
    return None


def _as_iso_date(value: Any) -> str:
    """Normalise whatever the portal calls a date into 'YYYY-MM-DD', or ''."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Epoch seconds or milliseconds; anything else is not a date.
        try:
            seconds = float(value)
            if seconds > 1e11:
                seconds /= 1000.0
            if seconds < 1e8:
                return ""
            return datetime.fromtimestamp(seconds).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    head = text.replace("/", "-").split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(head, fmt).date().isoformat()
        except ValueError:
            continue
    # "27.8." with no year — the portal does this in some views.
    parts = head.rstrip(".").split(".")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        try:
            today = date.today()
            return date(today.year, int(parts[1]), int(parts[0])).isoformat()
        except ValueError:
            return ""
    return ""


def _as_price(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return f"{value:g} Kč"
    text = str(value).strip()
    if not text:
        return ""
    # A bare number in a string still deserves the currency.
    try:
        return f"{float(text.replace(',', '.')):g} Kč"
    except ValueError:
        return text


# The portal labels the course with a code, not a word. Anything not listed is
# prettified by _pretty_kind rather than shown raw, so an OKbase upgrade that
# adds a course still reads as English and not as SHOUTED_SNAKE_CASE.
_KIND_LABELS = {
    "POLEVKA": "soup",
    "HLAVNI_JIDLO": "main course",
    "PRILOHA": "side dish",
    "SALAT": "salad",
    "DEZERT": "dessert",
    "NAPOJ": "drink",
    "SNIDANE": "breakfast",
    "VECERE": "dinner",
    "MINUTKA": "made to order",
}


def _pretty_kind(value: Any) -> str:
    text = _as_text(value)
    if not text:
        return ""
    known = _KIND_LABELS.get(text.strip().upper())
    if known:
        return known
    if text.isupper() and ("_" in text or text.isalpha()):
        return text.replace("_", " ").lower()
    return text


def _as_text(value: Any) -> str:
    """A label that may arrive as a string or as a nested code-book object."""
    if isinstance(value, dict):
        inner = _pick(value, _NAME_KEYS)
        if inner is not None:
            return _as_text(inner)
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(t for t in (_as_text(v) for v in value) if t)
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def parse_menu(payload: Any) -> dict[str, list[Meal]]:
    """Pull ``{'2026-08-27': [Meal, …]}`` out of whatever ``nacti-vse`` returned.

    Written as a walk rather than against a fixed schema because the schema is
    not published. Both shapes seen in this family of APIs work: a flat list of
    meals each carrying its own date, and a list of days each carrying a list of
    meals. A date found on an outer object is inherited by the meals inside it.
    """
    days: dict[str, list[Meal]] = {}
    seen: set[tuple[str, str]] = set()

    def walk(node: Any, day: str) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, day)
            return
        if not isinstance(node, dict):
            return

        found = _as_iso_date(_pick(node, _DATE_KEYS))
        if found:
            day = found

        name = _as_text(_pick(node, _NAME_KEYS))
        if name and day:
            key = (day, name.casefold())
            if key not in seen:
                seen.add(key)
                days.setdefault(day, []).append(Meal(
                    name=name,
                    price=_as_price(_pick(node, _PRICE_KEYS)),
                    kind=_pretty_kind(_pick(node, _KIND_KEYS)),
                ))

        for key, val in node.items():
            if _norm(key) in _NO_RECURSE:
                continue
            if isinstance(val, (list, dict)):
                walk(val, day)

    walk(payload, "")
    return days


# ---------------------------------------------------------------------------
# The cache file
# ---------------------------------------------------------------------------

USER_SETTINGS_FILENAME = "okbase.json"

# The canteen settings live with the USER, not with the program.
#
# Every other setting either travels on the share or is baked into the build.
# These can do neither: the sign-in is a DPAPI blob for one Windows account, so
# it must not go on the share, and it is one person's, so it must not be baked
# into a build everyone runs. That left `monitor_config.json` next to the exe —
# and the app has several homes (the source folder, and every version folder
# under C:\Dev\dist). A file beside the program is a DIFFERENT file for each of
# them, so every rebuild would arrive with no sign-in and the capture would have
# to be redone. `%APPDATA%` is the one place all of them, and the always-on
# listener, agree on. Same reasoning as alerting.run_status_path().
OKBASE_KEYS = (
    "okbase_enabled", "okbase_base_url", "okbase_username", "okbase_password",
    "okbase_session_cookie", "okbase_canteen_id", "okbase_user_id",
    "okbase_filter", "okbase_timeout_s", "okbase_refresh_hour",
    "okbase_keepalive_min", "okbase_account",
)


def user_settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Diagnostic" / USER_SETTINGS_FILENAME


def load_user_settings() -> dict:
    """The canteen settings for this Windows account. Never raises.

    A missing or half-written file only means "not set up yet", exactly like
    alerting.read_run_status().
    """
    try:
        with open(user_settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in OKBASE_KEYS}


def save_user_settings(values: dict) -> str:
    """Merge `values` into the user's canteen settings. Returns "" or an error.

    Merges rather than overwrites: the Settings dialog saves the fields a person
    typed, while a renewed session is saved on its own from a background job —
    two writers, as with the run-status file.
    """
    keep = {k: v for k, v in (values or {}).items() if k in OKBASE_KEYS}
    if not keep:
        return ""
    current = load_user_settings()
    current.update(keep)
    try:
        shared_pvs.write_json_atomic(user_settings_path(), current)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return ""


def local_cache_path() -> Path:
    """Per-user copy, always writable. Same reasoning as alerting.run_status_path:
    a file next to the program would be a different file for every installed
    copy."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Diagnostic" / CACHE_FILENAME


def shared_cache_path(override: str = "", cached_root: str = "",
                      timeout_s: float = 3.0) -> Optional[Path]:
    """Copy on the scratch share, so a listener on another PC can read it.

    Returns None when no share answered. **Touches the network** — call this
    from a worker thread, never from the UI thread (see the shared_pvs module
    docstring on the 48 s dead-host stall).
    """
    try:
        root, _src = shared_pvs.resolve_root(override, cached_root, timeout_s)
    except Exception:  # noqa: BLE001 - a missing share is not an error here
        return None
    if not root:
        return None
    folder = Path(root)
    if folder.name.lower() != shared_pvs.SHARED_SUBDIR.lower():
        folder = folder / shared_pvs.SHARED_SUBDIR
    return folder / CACHE_FILENAME


def read_cache_file(path: Path) -> dict:
    """The cache as stored, or {} for missing / half-written / foreign files."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("days") else {}


def read_cache(paths: list[Path]) -> dict:
    """The freshest usable cache among `paths`."""
    best: dict = {}
    for path in paths:
        data = read_cache_file(path)
        if not data:
            continue
        if not best or str(data.get("fetched", "")) > str(best.get("fetched", "")):
            best = data
    return best


def write_cache(cache: dict, paths: list[Path]) -> str:
    """Write the cache to every path given. Returns "" or a summary of failures."""
    problems = []
    for path in paths:
        try:
            shared_pvs.write_json_atomic(path, cache)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{path}: {exc}")
    return "; ".join(problems)


def build_cache(days: dict[str, list[Meal]], now: Optional[datetime] = None,
                filter_used: Any = None) -> dict:
    now = now or datetime.now()
    return {
        "version": 1,
        "source": "okbase",
        "fetched": now.isoformat(timespec="seconds"),
        "filter_used": filter_used,
        "days": {day: [m.as_dict() for m in meals]
                 for day, meals in sorted(days.items())},
    }


def cache_days(cache: dict) -> dict[str, list[Meal]]:
    raw = (cache or {}).get("days") or {}
    out: dict[str, list[Meal]] = {}
    if not isinstance(raw, dict):
        return out
    for day, meals in raw.items():
        if isinstance(meals, list):
            out[str(day)] = [Meal.from_dict(m) for m in meals
                             if isinstance(m, dict) and m.get("name")]
    return out


def cache_age_hours(cache: dict, now: Optional[datetime] = None) -> Optional[float]:
    """How long ago the cache was read from OKbase, or None if unknown."""
    stamp = (cache or {}).get("fetched")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return ((now or datetime.now()) - fetched).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Talking to the portal
# ---------------------------------------------------------------------------

def _session():
    """A requests session with cookies, or (None, error). Imported lazily so
    this module stays importable where requests is not installed."""
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return None, f"the requests library is missing ({exc})"
    s = requests.Session()
    # The portal is a normal public web server with a valid certificate, so
    # unlike the archiver client this keeps verify on.
    #
    # The three x-okbase-* headers are NOT optional. The front end sends them on
    # every call (captured from the browser), and they are how the back end knows
    # which data source, organisation and language the request belongs to — one
    # OKbase server can host several. A request without them is not the same
    # request, which is why guessing the body alone was never going to be enough.
    s.headers.update({
        "Accept": "*/*",
        "Content-Type": "application/json",
        "User-Agent": "Diagnostic/okbase-menu",
        "x-okbase-datasource": DATASOURCE_DEFAULT,
        "x-okbase-language": LANGUAGE_DEFAULT,
        "x-okbase-org-id": ORG_ID_DEFAULT,
    })
    return s, ""


def login_config(base: str = BASE_DEFAULT,
                 timeout: float = TIMEOUT_DEFAULT) -> tuple[dict, str]:
    """Which sign-in methods the instance offers. Needs no credentials."""
    s, err = _session()
    if s is None:
        return {}, err
    try:
        r = s.get(f"{base}/rest/authentication/web-login-config", timeout=timeout)
        if not (200 <= r.status_code < 300):
            return {}, f"HTTP {r.status_code}"
        return r.json(), ""
    except Exception as exc:  # noqa: BLE001
        return {}, str(exc)


ALIVE = "alive"
SIGNED_OUT = "signed-out"
UNREACHABLE = "unreachable"

# What a `requests` failure looks like when it is written out in full: about 400
# characters of nested urllib3 wrapping, ending in the one clause that says
# anything. It goes into the log and into a chat message, so it gets shortened to
# something a person can read at a glance.
_REASON_WORDS = (
    ("timed out", "it did not answer in time"),
    ("timeout", "it did not answer in time"),
    ("failed to resolve", "the address could not be looked up"),
    ("getaddrinfo", "the address could not be looked up"),
    ("name or service", "the address could not be looked up"),
    ("refused", "the connection was refused"),
    ("certificate", "the certificate was rejected"),
    ("ssl", "the secure connection failed"),
    ("proxy", "the proxy would not pass it through"),
)


def _short_reason(exc: Exception) -> str:
    """One readable clause out of a network exception."""
    text = f"{exc}".lower()
    for needle, plain in _REASON_WORDS:
        if needle in text:
            return plain
    one_line = " ".join(f"{exc}".split())
    return one_line[:80] or exc.__class__.__name__


def session_state(session, base: str = BASE_DEFAULT,
                  timeout: float = TIMEOUT_DEFAULT) -> tuple[str, str]:
    """(state, detail) for the stored sign-in. Never raises.

    Three answers, not two, and the distinction is the whole point. This
    endpoint answers 401 while signed out, so **only 401 and 403 mean signed
    out**. A 502 from the reverse proxy, a DNS blip, a VPN that dropped, a read
    that timed out — none of those say anything at all about the sign-in, and
    calling them an expiry is what sent a person off to paste a sign-in that was
    perfectly alive. Measured 2026-09-02: the saved cookie was reported expired
    by the app while this same cookie read the menu without complaint.
    """
    try:
        r = session.get(f"{base}/rest/app-info/serverovy-cas", timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return UNREACHABLE, _short_reason(exc)
    if 200 <= r.status_code < 300:
        return ALIVE, ""
    if r.status_code in (401, 403):
        return SIGNED_OUT, f"HTTP {r.status_code}"
    return UNREACHABLE, f"HTTP {r.status_code}"


def is_signed_in(session, base: str = BASE_DEFAULT,
                 timeout: float = TIMEOUT_DEFAULT) -> bool:
    """Signed in, yes or no. Anything that needs to tell a dead sign-in apart
    from an unreachable portal must call `session_state` instead."""
    return session_state(session, base, timeout)[0] == ALIVE


# The DTO name is OKbaseAuthenticationRequestUsernamePassword; its field names
# are not published, so try the plausible spellings and keep the one that works.
LOGIN_BODIES = (
    ("username", "password"),
    ("userName", "password"),
    ("login", "password"),
    ("jmeno", "heslo"),
)


def session_from_password(user: str, password: str, base: str = BASE_DEFAULT,
                          timeout: float = TIMEOUT_DEFAULT):
    """Sign in with a local OKbase username and password.

    Returns (session, "") or (None, reason). Accounts that only exist in
    Microsoft Entra ID cannot sign in this way — that is what
    ``session_from_cookie`` is for.
    """
    if not user or not password:
        return None, "no OKbase username or password is set"
    s, err = _session()
    if s is None:
        return None, err
    last = "sign-in refused"
    for user_key, pass_key in LOGIN_BODIES:
        try:
            r = s.post(f"{base}/rest/authentication/manual",
                       json={user_key: user, pass_key: password},
                       headers={"Accept": "text/plain"}, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return None, str(exc)
        if 200 <= r.status_code < 300 and is_signed_in(s, base, timeout):
            return s, ""
        last = f"HTTP {r.status_code} {(r.text or '')[:120]}".strip()
    return None, last


SESSION_EXPIRED = "the saved OKbase session has expired — sign in again"

# Deliberately a SEPARATE outcome from SESSION_EXPIRED, and the two must never be
# collapsed back into one. "expired" is a job for a person: go to the browser,
# copy the sign-in, paste it in. "unreachable" is a job for nobody: wait. Saying
# the first when the second is true is the bug this constant exists to end — it
# had the operator re-pasting a live sign-in because the portal had hiccuped.
PORTAL_UNREACHABLE = ("the OKbase portal could not be reached — the sign-in may "
                      "still be fine")


def is_expired(err: str) -> bool:
    """Does this error mean a person has to paste a new sign-in?

    Ask through here rather than comparing strings. An unreachable-portal message
    carries the reason on the end ("… (HTTP 502)"), so an exact `==` misses it
    and a `"expired" in err` test would match the wrong things — the listener
    used to do exactly that.
    """
    return (err or "") == SESSION_EXPIRED


def is_unreachable(err: str) -> bool:
    """Does this error mean only that the portal did not answer?"""
    return (err or "").startswith(PORTAL_UNREACHABLE)

# Asked for, in this order, when the stored session turns out to be dead.
#
# This is the part that makes the whole feature practical, and it was measured
# rather than assumed (2026-08-26): the captured cookies include a Shibboleth SP
# session (`_shibsession_…`), which is the REAL single sign-on. While that lives,
# a GET of the sso endpoint hands out a brand new JSESSIONID — HTTP 200, no
# redirect to Microsoft, no authenticator prompt. Verified both with the session
# cookie deleted and with a deliberately dead one.
#
# So the session cookie is disposable: what has to survive is `_shibsession_`,
# which is why parse_cookies keeps everything and why a failed sign-in is retried
# here before anyone is asked to paste anything again.
REVIVE_PATHS = (
    "/rest/authentication/sso",
    "/rest/authentication/remember-me",
)


def parse_cookies(text: str) -> dict[str, str]:
    """Cookies out of whatever was pasted in.

    Accepts a whole browser `Cookie:` header ("a=1; b=2"), a single
    "JSESSIONID=…" pair, or a bare session id on its own. **Every** cookie is
    kept, not just JSESSIONID: a "remember me" cookie among them is what lets
    the portal hand out a fresh session after the old one times out, which is
    the difference between refilling this field once and refilling it daily.
    """
    raw = (text or "").strip()
    if not raw:
        return {}
    if looks_like_curl(raw):
        # A whole "Copy as cURL" command. Splitting THAT on semicolons produced
        # cookie names like `curl 'https://…' -H 'accept` and saved them, so the
        # sign-in was mangled at the moment it was pasted and only failed later,
        # at the portal. Reading it properly is the difference between "it does
        # not work" and "it works".
        return parse_cookies(cookie_of(parse_curl(raw)[2]))
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    out: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, _, value = part.partition("=")
            name, value = name.strip(), value.strip()
            if name:
                out[name] = value
        elif not out:
            out["JSESSIONID"] = part      # a bare id pasted on its own
    return out


# --------------------------------------------------------------------------- #
# Reading whatever a person copied out of the browser
# --------------------------------------------------------------------------- #
#
# This lives here, not in okbase_capture.py where it started, so that the script,
# the Settings dialog and parse_cookies all read a paste the same way. One owner:
# a second copy in the GUI would drift, and the two would disagree about what a
# person had just pasted in.

_CURL_FLAGS = (" -h ", " --header", " -b ", " --cookie", " --data-raw",
               " --compressed", " -x ", " --request")


def looks_like_curl(text: str) -> bool:
    """Is this a copied cURL command rather than a Cookie line?

    Narrow on purpose. A cookie NAME cannot contain a space, so requiring either
    a leading `curl` or a space-delimited flag cannot misfire on a real Cookie
    line, however odd its contents.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if raw[:4].lower() == "curl":
        return True
    padded = " " + " ".join(raw.split()).lower() + " "
    return any(flag in padded for flag in _CURL_FLAGS)


def parse_curl(text: str) -> tuple[str, str, dict, str | None]:
    """(url, method, headers, body) out of a copied cURL command.

    Handles both flavours the browsers offer: the bash one (single quotes) and
    the Windows cmd one (^ continuations and ^" escapes).
    """
    text = (text or "").strip()
    if text[:4].lower() == "curl":
        text = text[4:]
    text = text.replace('^"', '"').replace("^\n", " ").replace("^%", "%")
    text = text.replace("\\\n", " ").replace("`\n", " ")
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        # An unbalanced quote — a paste that got cut off. Not an error to raise:
        # the caller says so in words (see read_paste).
        return "", "", {}, None

    url, method, body = "", None, None
    headers: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-H", "--header") and i + 1 < len(tokens):
            raw = tokens[i + 1]
            if ":" in raw:
                key, _, val = raw.partition(":")
                headers[key.strip()] = val.strip()
            i += 2
        elif tok in ("-b", "--cookie") and i + 1 < len(tokens):
            headers["cookie"] = tokens[i + 1]
            i += 2
        elif tok in ("-X", "--request") and i + 1 < len(tokens):
            method = tokens[i + 1]
            i += 2
        elif tok in ("--data-raw", "--data", "-d", "--data-binary",
                     "--data-ascii") and i + 1 < len(tokens):
            body = tokens[i + 1]
            i += 2
        elif tok.startswith("-"):
            i += 1                      # --compressed, --insecure, -s, …
        else:
            if not url and tok.startswith("http"):
                url = tok
            i += 1
    if method is None:
        method = "POST" if body is not None else "GET"
    return url, method, headers, body


def cookie_of(headers: dict) -> str:
    """The Cookie header out of a parsed request, whatever its capitalisation."""
    for key, val in (headers or {}).items():
        if str(key).lower() == "cookie":
            return val
    return ""


def _looks_like_session_id(text: str) -> bool:
    """Could this bare word be a session id? Tomcat's are 32 hex characters."""
    raw = (text or "").strip()
    return (16 <= len(raw) <= 128 and not any(c.isspace() for c in raw)
            and all(c.isalnum() or c in "-_." for c in raw))


@dataclass
class PastedSignIn:
    """Everything a paste can carry. Only `cookies` is ever essential."""
    cookies: str = ""
    jar: dict = field(default_factory=dict)
    base: str = ""                 # from the copied URL, when it carries /rest/
    user_id: str = ""
    canteen_id: str = ""
    filter_body: Optional[dict] = None
    source: str = ""               # "a copied cURL command" / "a Cookie line"
    problem: str = ""              # a sentence naming what to copy instead


def read_paste(text: str) -> PastedSignIn:
    """Make sense of whatever is on the clipboard. Never raises.

    Accepts a whole `Cookie:` line, a bare session id, or a Copy-as-cURL of the
    `nacti-vse` request in either flavour. The cURL is worth much more than the
    cookie alone: it also carries the person's userId, which canteen, and the
    exact request body the portal accepts — three things that otherwise have to
    be rediscovered by trial.
    """
    raw = (text or "").strip()
    if not raw:
        return PastedSignIn(problem="There is nothing on the clipboard.")

    if not looks_like_curl(raw):
        jar = parse_cookies(raw)
        # parse_cookies deliberately accepts a bare session id with no `=` in
        # it, which means it also accepts any old text. Here — where a person is
        # being told whether their paste worked — that has to be checked, or a
        # stray word gets saved as a sign-in and fails much later at the portal.
        if "=" not in raw and not _looks_like_session_id(raw):
            jar = {}
        if not jar:
            return PastedSignIn(problem=(
                "That does not look like a sign-in. Copy either the whole "
                "'Cookie' line from the browser's Network tab, or right-click "
                "the 'nacti-vse' request and choose Copy as cURL."))
        return PastedSignIn(cookies=cookie_line(jar), jar=jar,
                            source="a Cookie line")

    url, _method, headers, body = parse_curl(raw)
    jar = parse_cookies(cookie_of(headers))
    if not jar:
        return PastedSignIn(problem=(
            "That copied command carries no Cookie line, so it was a request "
            "that needed no sign-in. Pick the 'nacti-vse' row and copy that one."
            if url else
            "The copied command looks cut off — copy it again, all of it."))

    out = PastedSignIn(cookies=cookie_line(jar), jar=jar,
                       source="a copied cURL command")
    if "/rest/" in (url or ""):
        out.base = url.split("/rest/", 1)[0]
    if body:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            out.filter_body = parsed
            out.user_id = str(parsed.get("userId") or "")
            out.canteen_id = str(parsed.get("jidelnaId") or "")
    return out


def cookie_line(jar: dict[str, str]) -> str:
    """{name: value} back to `a=1; b=2`."""
    return "; ".join(f"{name}={value}" for name, value in (jar or {}).items())


def session_from_cookie(cookies: str, base: str = BASE_DEFAULT,
                        timeout: float = TIMEOUT_DEFAULT):
    """Reuse a sign-in done in a browser — the only way in for an account that
    signs in through Microsoft with an authenticator prompt.

    The session cookie itself is disposable. When it has timed out, the single
    sign-on cookie that came with it is used to get a new one (REVIVE_PATHS), so
    a paste keeps working long after the session it was taken from has died.
    """
    jar = parse_cookies(cookies)
    if not jar:
        return None, "no OKbase session is saved"
    s, err = _session()
    if s is None:
        return None, err
    host = _host(base)
    for name, value in jar.items():
        s.cookies.set(name, value, domain=host, path="/okbase")
    state, detail = session_state(s, base, timeout)
    if state == ALIVE:
        return s, ""
    if state == UNREACHABLE:
        # Not "expired", and no revive attempt: the revive paths live on the same
        # host that just failed to answer, so trying them is three more waits of
        # `timeout` seconds each for a certain nothing — a minute of the app
        # hanging on to reach a wrong conclusion.
        return None, f"{PORTAL_UNREACHABLE} ({detail})" if detail else PORTAL_UNREACHABLE
    for path in REVIVE_PATHS:
        try:
            s.get(f"{base}{path}", timeout=timeout)
        except Exception:  # noqa: BLE001 - just a chance, never an error
            continue
        state, detail = session_state(s, base, timeout)
        if state == ALIVE:
            return s, ""
        if state == UNREACHABLE:
            return None, (f"{PORTAL_UNREACHABLE} ({detail})" if detail
                          else PORTAL_UNREACHABLE)
    return None, SESSION_EXPIRED


def _host(base: str) -> str:
    text = base.split("//", 1)[-1]
    return text.split("/", 1)[0]


_START_KEYS = ("datumod", "weekstart", "datefrom", "od")
_END_KEYS = ("datumdo", "weekend", "dateto", "do")
# weekStart/weekEnd are the WEEK the portal is displaying; datumOd/datumDo is a
# much wider range around it (the browser asks for about six weeks at a time).
_WEEK_KEYS = ("weekstart", "weekend")


def _stamp(day: date) -> str:
    """'2026-08-24T00:00:00.000+02:00' — the exact shape the portal sends."""
    moment = datetime.combine(day, dtime(0, 0)).astimezone()
    offset = moment.strftime("%z") or "+0000"
    return f"{moment:%Y-%m-%dT%H:%M:%S}.000{offset[:3]}:{offset[3:]}"


def retarget_filter(template: dict, day_from: date, day_to: date,
                    week_of: Optional[date] = None) -> dict:
    """The captured request body, asking about OUR dates instead of its own.

    A body captured from the browser carries the dates of the day it was
    captured. Sending it back unchanged would fetch that week for ever, which is
    the one way a working capture still returns the wrong menu. Everything else
    in it — `userId`, `jidelnaId`, `objednavky` — is exactly what has to be kept.

    `week_of` is the week the portal should treat as the displayed one; it is
    NOT the same as `day_from`, because the range asked for reaches weeks either
    side of it (see FETCH_DAYS_BACK).
    """
    out = dict(template)
    monday = week_monday(week_of or day_from)
    for key, value in template.items():
        norm = _norm(key)
        if norm in _START_KEYS or norm in _END_KEYS:
            is_week = norm in _WEEK_KEYS
            if norm in _START_KEYS:
                day = monday if is_week else day_from
            else:
                day = monday + timedelta(days=6) if is_week else day_to
            # Keep whatever spelling the capture used.
            out[key] = _stamp(day) if (isinstance(value, str) and "T" in value) \
                else day.isoformat()
    return out


def filter_candidates(day_from: date, day_to: date, canteen_id: Any = None,
                      user_id: Any = None,
                      week_of: Optional[date] = None) -> list[dict]:
    """Request bodies for ``nacti-vse``, best first.

    The first one is the real shape, captured from the portal's own front end on
    2026-08-26; the rest are fallbacks in case a later OKbase version changes
    it. Whichever one the server accepts is remembered in `okbase_filter` and
    reused (retargeted to the dates being asked about) from then on.
    """
    monday = week_monday(week_of or day_from)
    real: dict = {
        "datumOd": _stamp(day_from),
        "datumDo": _stamp(day_to),
        "weekStart": _stamp(monday),
        "weekEnd": _stamp(monday + timedelta(days=6)),
        "jidelnaId": int(canteen_id) if str(canteen_id or "").isdigit() else 1,
        "objednavky": {},
    }
    if str(user_id or "").isdigit():
        real["userId"] = int(user_id)

    iso_from, iso_to = day_from.isoformat(), day_to.isoformat()
    bodies: list[dict] = [
        real,
        {k: v for k, v in real.items() if k != "userId"},
        {"datumOd": iso_from, "datumDo": iso_to},
        {"datumOd": f"{iso_from}T00:00:00", "datumDo": f"{iso_to}T23:59:59"},
        {},
    ]
    return bodies


def fetch_menu(session, day_from: date, day_to: date, base: str = BASE_DEFAULT,
               timeout: float = TIMEOUT_DEFAULT, canteen_id: Any = None,
               known_filter: Any = None, user_id: Any = None,
               week_of: Optional[date] = None):
    """Fetch the meal list for a date range.

    Returns (days, filter_used, "") on success, or ({}, None, reason).
    """
    url = f"{base}/rest/stravovani/objednavky/nacti-vse"
    bodies = filter_candidates(day_from, day_to, canteen_id, user_id, week_of)
    if isinstance(known_filter, dict) and known_filter:
        # Retargeted, never sent verbatim: the stored body carries the dates of
        # the day it was captured (see retarget_filter).
        first = retarget_filter(known_filter, day_from, day_to, week_of)
        bodies = [first] + [b for b in bodies if b != first]

    last = "the portal returned nothing usable"
    for body in bodies:
        try:
            r = session.post(url, json=body, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return {}, None, str(exc)
        if not (200 <= r.status_code < 300):
            last = f"HTTP {r.status_code} {(r.text or '')[:120]}".strip()
            if r.status_code in (401, 403):
                # Not a wrong body — a signed-out session, which refuses all five
                # of them identically. Trying the rest turns one failure into five
                # and one wait into five.
                return {}, None, last
            continue
        try:
            payload = r.json()
        except ValueError:
            last = "the portal did not answer with JSON"
            continue
        days = parse_menu(payload)
        if days:
            return days, body, ""
        last = "the portal answered, but no meals were found in the answer"
    return {}, None, last


def fetch_weeks(session, first_monday: date, weeks: int = FETCH_WEEKS,
                base: str = BASE_DEFAULT, timeout: float = TIMEOUT_DEFAULT,
                canteen_id: Any = None, known_filter: Any = None,
                user_id: Any = None):
    """One request per week, merged. Returns (days, filter_used, error).

    Measured on the real portal: the wide `datumOd`/`datumDo` range does NOT
    decide what comes back — `weekStart`/`weekEnd` does, and only that one week's
    meals arrive. A single wide request therefore looks like it worked and
    quietly returns the wrong week, which is exactly what happened the first
    time. So each week is asked for on its own.
    """
    merged: dict[str, list[Meal]] = {}
    used: Any = None
    problems: list[str] = []
    for index in range(max(1, weeks)):
        monday = first_monday + timedelta(weeks=index)
        days, body, err = fetch_menu(
            session, monday - timedelta(days=FETCH_DAYS_BACK),
            monday + timedelta(days=FETCH_DAYS_AHEAD), base, timeout,
            canteen_id, known_filter, user_id, week_of=monday)
        if days:
            merged.update(days)
            used = used or body
        elif err:
            problems.append(f"week of {monday.isoformat()}: {err}")
    if not merged:
        return {}, None, ("; ".join(problems)
                          or "the portal returned no meals for any week")
    return merged, used, ""


# ---------------------------------------------------------------------------
# The whole refresh job — this is what the worker calls
# ---------------------------------------------------------------------------

def cookie_header(session) -> str:
    """The session's cookies as a `Cookie:` line, ready to be saved again.

    Why save them back: a session id gets rotated (and remember-me mints a brand
    new one), so keeping what the server last handed out is what lets the next
    keepalive touch the session that is actually alive.
    """
    try:
        return "; ".join(f"{c.name}={c.value}" for c in session.cookies)
    except Exception:  # noqa: BLE001
        return ""


def merged_cookie_header(session, previous: str = "") -> str:
    """The session's cookies, but never FEWER names than we started with.

    A rotated JSESSIONID must be picked up — that is the whole reason the cookie
    line is written back at all. A cookie that has *disappeared* is a different
    matter: `requests` drops a cookie the moment the server sends a deleting
    `Set-Cookie`, and a Shibboleth SP does exactly that when it decides a session
    is invalid. Saving the shortened line then overwrites the stored one WITHOUT
    `_shibsession_…` — throwing away the single thing that lets the program mint
    itself a new session, and the single thing a re-paste exists to supply. The
    program was destroying its own asset and nothing noticed.

    So: new and changed values win, a vanished name is kept.
    """
    now = parse_cookies(cookie_header(session))
    if not now:
        return previous or ""
    kept = parse_cookies(previous)
    kept.update(now)
    return "; ".join(f"{name}={value}" for name, value in kept.items())


def secrets_resolved_cookie(settings: dict) -> str:
    """The stored sign-in as plaintext, or "" — the one place that decrypts it."""
    try:
        import secrets_util
        return secrets_util.resolve_secret(
            settings.get("okbase_session_cookie") or "")
    except Exception:  # noqa: BLE001
        return ""


def open_session(settings: dict):
    """Sign in however this PC can. Returns (session_or_None, base, timeout, error)."""
    base = str(settings.get("okbase_base_url") or BASE_DEFAULT).rstrip("/")
    timeout = float(settings.get("okbase_timeout_s") or TIMEOUT_DEFAULT)
    try:
        import secrets_util
        user = str(settings.get("okbase_username") or "")
        password = secrets_util.resolve_secret(settings.get("okbase_password") or "")
        cookie = secrets_resolved_cookie(settings)
    except Exception as exc:  # noqa: BLE001
        return None, base, timeout, f"could not read the saved OKbase sign-in ({exc})"

    # The cookie goes FIRST when there is one. It used to be the other way round,
    # and on this site the password form cannot work at all (the account exists
    # only in Entra ID), so every single attempt began by spending up to four
    # POSTs and four probes — 160 s at the default timeout — on a sign-in that
    # was never going to succeed, before touching the one that does. The password
    # path is kept for an instance where it IS the way in, and is now tried only
    # when the cookie is missing or has genuinely been rejected.
    session, err = None, ""
    if cookie:
        session, err = session_from_cookie(cookie, base, timeout)
        if session is None and is_unreachable(err):
            # The host is not answering. A password sign-in goes to that same
            # host, so it can only add more waiting to the same verdict.
            return None, base, timeout, err
    if session is None and user and password:
        session, pass_err = session_from_password(user, password, base, timeout)
        if session is None:
            err = err or pass_err
        else:
            err = ""
    if session is None:
        return None, base, timeout, (err or "no OKbase sign-in is configured")
    return session, base, timeout, ""


def keepalive(settings: dict) -> tuple[str, str]:
    """Touch the portal so a borrowed session does not idle out.

    Returns (cookie line to save, error). This is the difference between a paste
    that lasts half an hour and one that lasts until the portal restarts: a
    session times out on being UNUSED, and one cheap request resets that clock.
    """
    session, base, timeout, err = open_session(settings)
    if session is None:
        return "", err
    # No second probe: open_session established the session by probing it one
    # line ago, and asking again only doubled the chance of catching a hiccup and
    # calling a live sign-in dead.
    return merged_cookie_header(
        session, secrets_resolved_cookie(settings)), ""


def refresh(settings: dict, now: Optional[datetime] = None,
            write: bool = True, session_out: Optional[dict] = None
            ) -> tuple[dict, str]:
    """Sign in, fetch two weeks, write the cache. Returns (cache, error).

    Never raises. `settings` is the app's settings dict; the secrets are
    resolved here so no caller has to know how they are stored. When
    `session_out` is given it receives ``{"cookies": "<Cookie line>"}`` so the
    caller can save a rotated session back — optional, because most callers do
    not care.
    """
    now = now or datetime.now()
    session, base, timeout, err = open_session(settings)
    if session is None:
        return {}, err
    if session_out is not None:
        session_out["cookies"] = merged_cookie_header(
            session, secrets_resolved_cookie(settings))

    days, used, err = fetch_weeks(
        session, week_monday(now.date()), FETCH_WEEKS, base, timeout,
        settings.get("okbase_canteen_id") or None,
        settings.get("okbase_filter") or None,
        settings.get("okbase_user_id") or None)
    if not days:
        return {}, err

    cache = build_cache(days, now, used)
    if write:
        paths = [local_cache_path()]
        shared = shared_cache_path(
            str(settings.get("shared_pv_list_path") or ""),
            str(settings.get("_shared_pv_root_cache") or ""),
            float(settings.get("shared_pv_list_timeout_s") or 3.0))
        if shared is not None:
            paths.append(shared)
        problem = write_cache(cache, paths)
        if problem:
            return cache, f"menu fetched, but saving it failed: {problem}"
    return cache, ""


def load_cache(settings: Optional[dict] = None, use_share: bool = True) -> dict:
    """The best cache this PC can see. **Blocking** — worker thread only."""
    settings = settings or {}
    paths = [local_cache_path()]
    if use_share:
        shared = shared_cache_path(
            str(settings.get("shared_pv_list_path") or ""),
            str(settings.get("_shared_pv_root_cache") or ""),
            float(settings.get("shared_pv_list_timeout_s") or 3.0))
        if shared is not None:
            paths.append(shared)
    return read_cache(paths)


# ---------------------------------------------------------------------------
# Rendering the reply
# ---------------------------------------------------------------------------

def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _day_title(day: date) -> str:
    return f"{DAY_NAMES[day.weekday()]} {day.strftime('%d.%m.')}"


# Courses get a heading of their own rather than a label tacked onto each line:
# the portal lists the soups first and the label at the end of the line put the
# word "soup" where the eye looks for the price.
_COURSE_ORDER = ("soup", "main course", "side dish", "salad", "dessert",
                 "drink", "breakfast", "dinner", "made to order")
_COURSE_HEADING = {
    "soup": "🥣 Soups",
    "main course": "🍛 Main courses",
    "side dish": "🥔 Side dishes",
    "salad": "🥗 Salads",
    "dessert": "🍰 Desserts",
    "drink": "☕ Drinks",
    "breakfast": "🥐 Breakfast",
    "dinner": "🌙 Dinner",
    "made to order": "👨‍🍳 Made to order",
}


def _course_rank(kind: str) -> int:
    try:
        return _COURSE_ORDER.index(kind)
    except ValueError:
        return len(_COURSE_ORDER)       # anything new goes last, never dropped


def _heading(kind: str) -> str:
    return _COURSE_HEADING.get(kind) or (f"🍴 {kind.capitalize()}" if kind
                                         else "🍴 Other")


def render_meals(meals: list[Meal], lang: str = "both") -> list[str]:
    """The meals of one day: grouped by course, numbered inside each group.

    `lang` is "cs", "en" or "both". In "both" the English name goes on its own
    indented line under the Czech one — side by side they were separated only by
    a slash, which is what made the list unreadable when half the meals have one
    language and half have two.
    """
    groups: dict[str, list[Meal]] = {}
    for meal in meals:
        groups.setdefault(meal.kind, []).append(meal)

    lines: list[str] = []
    for kind in sorted(groups, key=_course_rank):
        if lines:
            lines.append("")
        lines.append(f"**{_heading(kind)}**")
        for number, meal in enumerate(groups[kind], start=1):
            title, second = meal.titles(lang)
            price = f" — {meal.price}" if meal.price else ""
            lines.append(f"{number}) **{title}**{price}")
            if second and second != title:
                lines.append(f"    _{second}_")
    return lines


def _stale_note(cache: dict, now: datetime) -> str:
    stamp = str((cache or {}).get("fetched") or "")
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    hours = (now - fetched).total_seconds() / 3600.0
    if hours < STALE_AFTER_HOURS:
        return ""
    if hours < 48:
        age = f"{hours:.0f} h"
    else:
        age = f"{hours / 24:.0f} days"
    return (f"**⚠ This menu was last read {age} ago "
            f"({fetched:%Y-%m-%d %H:%M}) — it may be out of date.**")


def _footer(cache: dict) -> str:
    stamp = str((cache or {}).get("fetched") or "")
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return ""
    return f"_Read from OKbase {fetched:%Y-%m-%d %H:%M}._"


def render_day(cache: dict, day: date, now: Optional[datetime] = None,
               lang: str = "both") -> str:
    now = now or datetime.now()
    days = cache_days(cache)
    if not days:
        return ("🍽 I have no menu saved yet. It is read from OKbase once a day; "
                "`/food refresh` asks for it now.")

    meals = days.get(day.isoformat())
    parts = [p for p in (_stale_note(cache, now),) if p]
    if meals:
        parts.append(f"**🍽 Menu — {_day_title(day)}**")
        parts.append("")
        parts.extend(render_meals(meals, lang))
    elif _covers(days, day):
        parts.append(f"**🍽 {_day_title(day)}** — no meals offered.")
    else:
        parts.append(f"**🍽 {_day_title(day)}** — I have no menu for that day. "
                     f"{_known_range(days)}")
    footer = _footer(cache)
    if footer:
        parts.append("")        # Webex glues an italic line onto the list above
        parts.append(footer)
    return "\n".join(parts)


def render_week(cache: dict, monday: date, now: Optional[datetime] = None,
                lang: str = "both") -> str:
    now = now or datetime.now()
    days = cache_days(cache)
    if not days:
        return ("🍽 I have no menu saved yet. It is read from OKbase once a day; "
                "`/food refresh` asks for it now.")

    parts = [p for p in (_stale_note(cache, now),) if p]
    sunday = monday + timedelta(days=6)
    parts.append(f"**🍽 Menu {monday.strftime('%d.%m.')} – "
                 f"{sunday.strftime('%d.%m.%Y')}**")
    shown = 0
    for offset in range(7):
        day = monday + timedelta(days=offset)
        meals = days.get(day.isoformat())
        if not meals:
            continue
        shown += 1
        parts.append("")
        parts.append(f"**— {_day_title(day)} —**")
        parts.extend(render_meals(meals, lang))
    if not shown:
        parts.append("")
        parts.append(f"I have no menu for that week. {_known_range(days)}")
    footer = _footer(cache)
    if footer:
        parts.append("")
        parts.append(footer)
    return "\n".join(parts)


def render_status(cache: dict, now: Optional[datetime] = None) -> str:
    """What the saved menu holds — the part that can be said without the app.

    The app answers `/food status` with more than this (what it is doing right
    now, whether the sign-in still works); this is what the always-on listener
    can honestly report when the app is closed.
    """
    now = now or datetime.now()
    days = cache_days(cache)
    lines = ["**🍽 Canteen menu — what I have saved**"]
    if days:
        keys = sorted(days)
        meals = sum(len(v) for v in days.values())
        lines.append(f"- **Days:** {len(days)} ({keys[0]} to {keys[-1]}), "
                     f"{meals} meal(s) in total")
        age = cache_age_hours(cache, now)
        stamp = str(cache.get("fetched", ""))[:16].replace("T", " ")
        if age is not None:
            lines.append(f"- **Read from OKbase:** {stamp} ({age:.0f} h ago)")
        if age is not None and age >= STALE_AFTER_HOURS:
            lines.append("- ⚠ That is old enough to be out of date.")
    else:
        lines.append("- **Days:** none saved yet")
    lines.append("- I am the always-on listener, so I only read what Diagnostic "
                 "saved. Ask again with Diagnostic open for the sign-in state.")
    return "\n".join(lines)


def _covers(days: dict[str, list[Meal]], day: date) -> bool:
    """Was this day part of what was fetched? A weekday inside the fetched span
    with no meals is a closed canteen; a day outside it is simply unknown."""
    if not days:
        return False
    keys = sorted(days)
    return keys[0] <= day.isoformat() <= keys[-1]


def _known_range(days: dict[str, list[Meal]]) -> str:
    if not days:
        return ""
    keys = sorted(days)
    first = datetime.strptime(keys[0], "%Y-%m-%d").date()
    last = datetime.strptime(keys[-1], "%Y-%m-%d").date()
    if first == last:
        return f"I only have {first.strftime('%d.%m.%Y')}."
    return (f"I have {first.strftime('%d.%m.')} to "
            f"{last.strftime('%d.%m.%Y')}.")


# ---------------------------------------------------------------------------
# The /food command
# ---------------------------------------------------------------------------

WEEKDAY_WORDS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

FOOD_HELP = (
    "**/food** — the canteen menu, as read from OKbase.\n"
    "- `/food` — today until 14:30, and the next serving day after that, "
    "because by then today's lunch is over\n"
    "- `/food today` — today whatever the time is\n"
    "- `/food tomorrow` — tomorrow\n"
    "- `/food week` — this week; `/food next week` — the one after\n"
    "- `/food monday` — that weekday of this week\n"
    "- `/food 27.8.` — that date\n"
    "- `/food refresh` — read it from OKbase again now\n"
    "- `/food status` — what I have saved, what I am doing, and whether the "
    "OKbase sign-in still works\n"
    "\n"
    "Add `; cz` for the Czech names only, `; en` for the English ones. Without "
    "either you get both, the English one under the Czech. It combines with "
    "everything else: `/food week; en`, `/food friday; cz`."
)


LANG_WORDS = {
    "cz": "cs", "cs": "cs", "cesky": "cs", "czech": "cs",
    "en": "en", "eng": "en", "english": "en", "anglicky": "en",
    "both": "both", "all": "both", "obojí": "both", "oboji": "both",
}


# Lunch is served and gone by mid-afternoon, so from here on a bare `/food` is
# no longer asking what there is today — it is asking what there is tomorrow.
# Only a bare `/food` moves: `/food today` still means today.
LUNCH_OVER_AT = dtime(14, 30)


@dataclass
class FoodRequest:
    """What `/food …` asked for."""
    mode: str = "day"                 # day | week | refresh | status | help
    day: Optional[date] = None
    monday: Optional[date] = None
    lang: str = "both"                # cs | en | both
    error: str = ""
    # True only for a bare `/food`, where no day was named. That is the one case
    # allowed to move itself on past LUNCH_OVER_AT — see next_food_day.
    default_day: bool = False


def parse_food_args(args: str, today: Optional[date] = None) -> FoodRequest:
    """Read the words after `/food`. Never raises; unknown words come back as
    an error message written for the chat."""
    today = today or date.today()
    # ';' and ',' are the command language's separators, so they are just
    # spacing here — "/food; cz", "/food week; en" and "/food en" are one thing.
    cleaned = (args or "").lower().replace(",", " ").replace(";", " ")
    words = [w for w in cleaned.split() if w]

    # The language can be asked for anywhere in the line; take it out first so
    # everything after it still reads as a day.
    lang = "both"
    rest = []
    for word in words:
        if word in LANG_WORDS:
            lang = LANG_WORDS[word]
        else:
            rest.append(word)
    words = rest

    if not words:
        return FoodRequest("day", day=today, lang=lang, default_day=True)

    if words[0] in ("refresh", "reload", "update"):
        return FoodRequest("refresh", lang=lang)
    if words[0] in ("status", "info", "state"):
        return FoodRequest("status", lang=lang)
    if words[0] in ("help", "?"):
        return FoodRequest("help", lang=lang)

    offset_weeks = 0
    if words[0] in ("next", "this", "last", "previous", "prev"):
        offset_weeks = {"next": 1, "this": 0, "last": -1,
                        "previous": -1, "prev": -1}[words[0]]
        words = words[1:] or ["week"]

    if words[0] in ("week", "weekly"):
        return FoodRequest(
            "week", monday=week_monday(today) + timedelta(weeks=offset_weeks),
            lang=lang)
    if words[0] in ("today", "now"):
        return FoodRequest("day", day=today, lang=lang)
    if words[0] in ("tomorrow",):
        return FoodRequest("day", day=today + timedelta(days=1), lang=lang)
    if words[0] in ("yesterday",):
        return FoodRequest("day", day=today - timedelta(days=1), lang=lang)
    if words[0] in WEEKDAY_WORDS:
        monday = week_monday(today) + timedelta(weeks=offset_weeks)
        return FoodRequest("day", lang=lang,
                           day=monday + timedelta(days=WEEKDAY_WORDS[words[0]]))

    parsed = _parse_day_word(words[0], today)
    if parsed is not None:
        return FoodRequest("day", day=parsed, lang=lang)
    return FoodRequest("day", lang=lang,
                       error=f"I don't understand '{words[0]}'.")


def _parse_day_word(word: str, today: date) -> Optional[date]:
    """'27.8.', '27.8.2026' or '2026-08-27'."""
    text = word.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    parts = text.rstrip(".").split(".")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        try:
            return date(today.year, int(parts[1]), int(parts[0]))
        except ValueError:
            return None
    return None


def answer_food(args: str, cache: dict, now: Optional[datetime] = None) -> str:
    """The whole reply to a `/food …` line, given a cache. Never raises.

    `refresh` is not handled here: only a process holding the credentials can do
    that, so the caller sees mode == "refresh" and decides.
    """
    now = now or datetime.now()
    req = parse_food_args(args, now.date())
    if req.error:
        return f"⚠ {req.error}\n\n{FOOD_HELP}"
    if req.mode == "help":
        return FOOD_HELP
    if req.mode == "status":
        return render_status(cache, now)
    if req.mode == "week":
        return render_week(cache, req.monday or week_monday(now.date()), now,
                           req.lang)
    day = req.day or now.date()
    if req.default_day:
        day = next_food_day(cache, now)
    return render_day(cache, day, now, req.lang)


def next_food_day(cache: dict, now: Optional[datetime] = None) -> date:
    """Which day a bare `/food` should show.

    Before LUNCH_OVER_AT, today. After it, lunch is over and the useful answer
    is the next day there is anything to eat — which on a Friday afternoon is
    Monday, not an empty Saturday. The saved menu decides that, rather than a
    weekday rule: a closed canteen on a working day (a holiday, a shutdown) has
    no meals in it either, and skipping it is just as right.

    The heading always names the day it is showing, so nothing here is silent.
    """
    now = now or datetime.now()
    today = now.date()
    if now.time() < LUNCH_OVER_AT:
        return today
    days = cache_days(cache)
    for step in range(1, 5):            # tomorrow, then over a weekend at most
        day = today + timedelta(days=step)
        if days.get(day.isoformat()):
            return day
    return today + timedelta(days=1)


# ---------------------------------------------------------------------------
# Shell use: python okbase_menu.py --check | --today | --week | --refresh
# ---------------------------------------------------------------------------

def _cli_settings() -> dict:
    """Settings straight from monitor_config.json, without importing the GUI."""
    try:
        import cpva_api
        path = cpva_api.get_app_dir() / "monitor_config.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        settings = dict(data.get("settings") or {})
    except Exception as exc:  # noqa: BLE001
        print(f"(could not read monitor_config.json: {exc})")
        settings = {}
    try:
        import notify_provision
        settings.update(notify_provision.load())
    except Exception:  # noqa: BLE001
        pass
    # …and last, the canteen sign-in from the Windows account's own file, which
    # is where it actually lives (OKBASE_KEYS). Without this the shell commands
    # were reading whatever stale copy monitor_config.json still held and could
    # report "no sign-in" while the app was signed in perfectly well — the same
    # precedence the app (monitor_tab.load_config) and the listener already use.
    settings.update(load_user_settings())
    return settings


def describe_cookies(jar: dict[str, str]) -> list[str]:
    """Cookie NAMES and lengths, never values.

    So that the answer to "is my sign-in still there" can be printed, screenshot
    and pasted into a chat without handing anybody a live session. The transient
    sign-on cookies are counted rather than listed — there are about twenty of
    them and none of them matters.
    """
    lines = [f"cookies : {len(jar)} found (values are never shown)"]
    hidden = 0
    sso = False
    for name, value in jar.items():
        low = name.lower()
        if low.startswith("_opensaml_req_ss"):
            hidden += 1
            continue
        note = ""
        if low.startswith("_shibsession_"):
            note = "  <-- the company sign-on; this is the one that matters"
            sso = True
        elif low == "jsessionid":
            note = "  <-- the session itself (renewable, so not the important one)"
        lines.append(f"          {name}  ({len(value)} characters){note}")
    if hidden:
        lines.append(f"          … and {hidden} transient sign-on cookies")
    if not sso and jar:
        lines.append("  WARNING: no company sign-on cookie in there. It will work,")
        lines.append("  but only until the session times out — copy the whole")
        lines.append("  Cookie line, not just the first entry.")
    return lines


def _cli_check(settings: dict, now: datetime) -> int:
    """`--check`: is the saved sign-in alive? The five-second answer.

    Written for the question that actually gets asked — "I rebuilt Diagnostic, do
    I have to paste the cookie in again?" — because guessing at it was costing a
    DevTools expedition every time. The answer is almost always no: the sign-in
    lives in the Windows account's own file and every build reads that same one.
    """
    print(f"settings file : {user_settings_path()}")
    print(f"sign-in saved : {'yes' if secrets_resolved_cookie(settings) else 'NO'}")
    print(f"switched on   : {'yes' if settings.get('okbase_enabled') else 'no'}")
    for line in describe_cookies(parse_cookies(secrets_resolved_cookie(settings))):
        print(line)

    base = str(settings.get("okbase_base_url") or BASE_DEFAULT).rstrip("/")
    print(f"\nasking {base} …")
    session, base, timeout, err = open_session(settings)
    if session is not None:
        print("  the sign-in WORKS — nothing to paste.")
        verdict = 0
    elif is_unreachable(err):
        print(f"  the PORTAL did not answer: {err}")
        print("  This says nothing about the sign-in. Try again later; do not")
        print("  paste anything yet.")
        verdict = 2
    else:
        print(f"  the sign-in is NOT usable: {err}")
        print("  This one does need a person: copy the sign-in out of the browser")
        print("  again (Settings -> Canteen menu, or okbase_capture.py).")
        verdict = 1

    cache = load_cache(settings)
    days = cache_days(cache)
    if days:
        keys = sorted(days)
        age = cache_age_hours(cache)
        print(f"\nsaved menu    : {len(days)} day(s), {keys[0]} to {keys[-1]}"
              + (f", read {age:.0f} h ago" if age is not None else ""))
    else:
        print("\nsaved menu    : none yet")
    return verdict


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:]]
    settings = _cli_settings()
    now = datetime.now()

    if "--check" in args:
        return _cli_check(settings, now)

    if "--login-config" in args:
        cfg, err = login_config(
            str(settings.get("okbase_base_url") or BASE_DEFAULT).rstrip("/"))
        print(err or json.dumps(cfg, indent=2, ensure_ascii=False))
        return 0 if not err else 1

    if "--refresh" in args:
        cache, err = refresh(settings, now)
        if err:
            print(f"refresh failed: {err}")
        if not cache:
            return 1
        print(f"fetched {len(cache.get('days') or {})} day(s); "
              f"filter used: {json.dumps(cache.get('filter_used'), ensure_ascii=False)}")
        print(f"local cache: {local_cache_path()}")
    else:
        cache = load_cache(settings)

    if "--week" in args:
        print(render_week(cache, week_monday(now.date()), now))
    elif "--next-week" in args:
        print(render_week(cache, week_monday(now.date()) + timedelta(days=7), now))
    elif "--refresh" in args and len(args) == 1:
        print(render_day(cache, now.date(), now))
    else:
        rest = [a for a in args if not a.startswith("--")]
        print(answer_food(" ".join(rest), cache, now))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
