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
from typing import Any, Iterable, Optional

import shared_pvs

BASE_DEFAULT = "https://elieric.okbase.cz/okbase/service"
# The page a PERSON opens. Not the REST base: the browser sign-in window has to
# land somewhere recognisable, and this is the page they would open anyway. Kept
# here with the rest of the portal's addresses so there is one owner of them.
MENU_PAGE_DEFAULT = ("https://elieric.okbase.cz/okbase/web-client/web/"
                     "objednavky-jidel")
CACHE_FILENAME = "menu_cache.json"
TIMEOUT_DEFAULT = 20.0
# The cheapest question that has a different answer signed in and signed out —
# 401 while signed out. Named because edge_cdp asks it too, from inside the
# browser window, to tell a live sign-in from a cookie left over from yesterday.
ALIVE_PATH = "/rest/app-info/serverovy-cas"

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
    # The two fields ordering needs. `item_id` is `listky[day].polozky[].id` —
    # the number the portal wants back when this meal is ordered — and `code` is
    # the raw course code (`POLEVKA`, `HLAVNI_JIDLO`) it is filed under in the
    # order, which is NOT the same string as the human `kind` above. Kept in the
    # cache so a `/food order 2` acts on the meal the reply actually printed,
    # even if the menu were re-published in between; a cache written before this
    # existed simply has no ids, and ordering then re-reads the day live.
    item_id: Optional[int] = None
    code: str = ""

    def as_dict(self) -> dict:
        out = {"name": self.name, "price": self.price, "kind": self.kind}
        if self.item_id is not None:
            out["item_id"] = self.item_id
        if self.code:
            out["code"] = self.code
        return out

    @staticmethod
    def from_dict(d: dict) -> "Meal":
        raw_id = d.get("item_id")
        return Meal(name=str(d.get("name") or ""),
                    price=str(d.get("price") or ""),
                    kind=str(d.get("kind") or ""),
                    item_id=int(raw_id) if str(raw_id or "").isdigit() else None,
                    code=str(d.get("code") or ""))

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

    def walk(node: Any, day: str, item_id: Optional[int]) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, day, item_id)
            return
        if not isinstance(node, dict):
            return

        found = _as_iso_date(_pick(node, _DATE_KEYS))
        if found:
            day = found

        # The id ordering needs sits one level ABOVE the name: a menu row is
        # ``{id, poradi, jidlo: {nazev, typ, …}}``, so the number belongs to the
        # row and the name to its `jidlo`. Carry it down into the child rather
        # than looking for it beside the name, where it is not.
        if isinstance(node.get("jidlo"), dict):
            raw = node.get("id")
            if isinstance(raw, int):
                item_id = raw

        name = _as_text(_pick(node, _NAME_KEYS))
        if name and day:
            key = (day, name.casefold())
            if key not in seen:
                seen.add(key)
                days.setdefault(day, []).append(Meal(
                    name=name,
                    price=_as_price(_pick(node, _PRICE_KEYS)),
                    kind=_pretty_kind(_pick(node, _KIND_KEYS)),
                    item_id=item_id,
                    code=_course_code(node),
                ))

        for key, val in node.items():
            if _norm(key) in _NO_RECURSE:
                continue
            if isinstance(val, (list, dict)):
                walk(val, day, item_id)

    walk(payload, "", None)
    return days


# The course codes the order request files a meal under. Confirmed live: the
# order's `polozkyIdMap` is keyed by exactly these, one meal per course.
COURSE_CODES = ("POLEVKA", "HLAVNI_JIDLO")


def _course_code(node: dict) -> str:
    """The raw course code (`POLEVKA`, `HLAVNI_JIDLO`) of a menu row.

    Deliberately NOT `_pretty_kind`'s output: that is wording for a person, and
    the order request is matched by the portal on the code. Anything unexpected
    is kept verbatim rather than dropped, so a course OKbase adds later still
    orders instead of silently vanishing.
    """
    raw = _pick(node, ("typ", "jidlotyp", "typjidla"))
    if isinstance(raw, dict):
        raw = _pick(raw, ("kod", "code", "nazev", "name"))
    text = str(raw or "").strip()
    return text.upper().replace(" ", "_").replace("-", "_") if text else ""


# What `listky[day].stav` means. Only these two have ever been seen, and the
# distinction is the whole cutoff story: `ZVEREJNENY` is a day still taking
# orders, `UZAVRENY` one that has closed. The closing time is NOT fixed — it is
# nominally 10:00 but in practice comes earlier some days — so this state, read
# at the moment of ordering, is the only thing allowed to decide. Never a clock.
DAY_OPEN, DAY_CLOSED = "open", "closed"
_DAY_STATES = {"ZVEREJNENY": DAY_OPEN, "UZAVRENY": DAY_CLOSED}


def parse_day_states(payload: Any) -> dict[str, str]:
    """``{'2026-09-11': 'open'}`` — which days still take orders."""
    out: dict[str, str] = {}
    tickets = (payload or {}).get("listky") if isinstance(payload, dict) else None
    if not isinstance(tickets, dict):
        return out
    for day, node in tickets.items():
        iso = _as_iso_date(day) or _as_iso_date((node or {}).get("datumVydeje"))
        if not iso or not isinstance(node, dict):
            continue
        raw = str(node.get("stav") or "").strip().upper()
        out[iso] = _DAY_STATES.get(raw, raw.lower() or DAY_CLOSED)
    return out


@dataclass
class Order:
    """One day of this person's own order, as the portal reports it."""
    day: str
    order_id: Optional[int] = None
    state: str = ""              # OBJEDNANO / ODEBRANO / NEODEBRANO
    item_ids: tuple[int, ...] = ()
    none_ordered: bool = True    # the portal's `zadna`

    @property
    def ordered(self) -> bool:
        return bool(self.item_ids) and not self.none_ordered


def parse_orders(payload: Any) -> dict[str, Order]:
    """``{'2026-09-11': Order}`` out of the `objednavky` half of `nacti-vse`.

    The same answer that carries the menu carries the person's own orders, so
    this costs no extra request. Every `nazev` in this half is null — the items
    are ids only — which is why the menu half has to supply the names.
    """
    out: dict[str, Order] = {}
    rows = (payload or {}).get("objednavky") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return out
    for day, entries in rows.items():
        iso = _as_iso_date(day)
        if not iso:
            continue
        if not isinstance(entries, list) or not entries:
            out[iso] = Order(day=iso)
            continue
        node = entries[0] if isinstance(entries[0], dict) else {}
        ids = tuple(int(p["id"]) for p in (node.get("polozky") or [])
                    if isinstance(p, dict) and isinstance(p.get("id"), int))
        out[iso] = Order(
            day=iso,
            order_id=(node.get("id") if isinstance(node.get("id"), int) else None),
            state=str(node.get("stav") or ""),
            item_ids=ids,
            none_ordered=bool(node.get("zadna")) or not ids,
        )
    return out


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
    # Who is allowed to order lunch through the bot. It belongs with the
    # sign-in and nowhere else: the sign-in is ONE person's, so an order placed
    # with it spends that person's money, and a Webex room is a shared place —
    # everyone in it can type. Empty means nobody, which is the right default
    # for a build handed to somebody else.
    "okbase_order_emails",
    # The unused single-use ordering codes, as one-way hashes (see
    # new_order_codes). Empty means no order is accepted at all.
    "okbase_order_codes",
)


# ---------------------------------------------------------------------------
# The single-use codes an order has to carry
# ---------------------------------------------------------------------------
#
# The problem this solves: a chat message is permanent. A fixed password typed
# into a room stays readable to everyone who can see that room, for ever, so
# after its first use it protects nothing. A one-time code does not have that
# problem — what stays visible has already been spent.
#
# Why a hundred made-up words rather than a password or an authenticator app:
#
#   * the operator has no way to add entries to the company authenticator, so a
#     rotating-code app is not available here;
#   * a real dictionary word would be far too easy to guess. There are only a
#     few thousand common words, a hundred of them are live at any time, so one
#     in a few dozen guesses would land. These are built from syllables instead
#     — roughly a million possibilities — while still reading and typing like a
#     word, which a string of digits does not;
#   * they are stored as one-way hashes and NOT as DPAPI blobs. Every other
#     secret here is DPAPI-encrypted because the program must hand the real
#     value to somebody else: the portal wants the actual cookie. A code is
#     different — nothing ever needs it back, only a yes/no on one just typed.
#     So it is hashed, and the stored file cannot be turned back into the codes
#     by anything, this program included.
#
# A spent code is DELETED from the file rather than marked, so there is nothing
# to un-mark and no flag to get wrong.

ORDER_CODE_COUNT = 100
ORDER_CODE_SYLLABLES = 3
# From here on, every successful order says how many codes are left. Said only
# when it starts to matter: a count under every reply would be noise, and
# finding out the list is empty at the moment you want lunch would not.
CODES_LOW_AT = 10
# Plain ASCII on purpose: these get typed on a phone keyboard, and a diacritic
# is both awkward there and one more thing that can arrive differently encoded.
_CODE_CONSONANTS = "bcdfghjklmnprstvz"
_CODE_VOWELS = "aeiouy"

_CODE_ROUNDS = 200_000
_PBKDF2_ROUNDS = 240_000


def hash_password(plain: str, rounds: int = _PBKDF2_ROUNDS) -> str:
    """`pbkdf2$<rounds>$<salt>$<hash>`, or "" for an empty input."""
    import base64
    import hashlib
    import secrets as _secrets
    plain = plain or ""
    if not plain:
        return ""
    salt = _secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, rounds)
    return "pbkdf2${}${}${}".format(
        rounds,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"))


def normalise_code(text: str) -> str:
    """A typed code reduced to what is compared: letters only, lower case.

    So that a code read off a phone still works when it arrives with a stray
    dash, a capital first letter or a trailing full stop.
    """
    return "".join(ch for ch in (text or "").lower() if ch.isalpha())


def new_order_code() -> str:
    """One pronounceable made-up word, e.g. "bakoli"."""
    import secrets as _secrets
    return "".join(_secrets.choice(_CODE_CONSONANTS) + _secrets.choice(_CODE_VOWELS)
                   for _ in range(ORDER_CODE_SYLLABLES))


# The list is stored as ONE salt plus a fingerprint per code:
#
#     {"salt": "<base64>", "rounds": 200000, "hashes": ["<base64>", …]}
#
# One salt for the whole list rather than one each, because checking a typed
# code then costs a single derivation and a set lookup instead of up to a
# hundred of them — which is the difference between telling somebody their code
# is wrong at once and going to the portal first to find out. The salt being
# shared does not weaken this: what it protects against is somebody reading the
# file, and anyone who can read that file can already decrypt the sign-in
# sitting next to it and order lunch with no code at all.


def _code_digest(code: str, salt: bytes, rounds: int) -> str:
    import base64
    import hashlib
    return base64.b64encode(hashlib.pbkdf2_hmac(
        "sha256", code.encode("utf-8"), salt, rounds)).decode("ascii")


def new_order_codes(count: int = ORDER_CODE_COUNT) -> tuple[list[str], dict]:
    """(the codes to give the person, the blob to store).

    The plain codes exist only in the caller's hands: they are shown once and
    never written anywhere by this module.
    """
    import base64
    import secrets as _secrets
    words: list[str] = []
    seen: set[str] = set()
    while len(words) < max(1, count):
        word = new_order_code()
        if word not in seen:           # two identical codes would spend as one
            seen.add(word)
            words.append(word)
    salt = _secrets.token_bytes(16)
    return words, {
        "salt": base64.b64encode(salt).decode("ascii"),
        "rounds": _CODE_ROUNDS,
        "hashes": [_code_digest(w, salt, _CODE_ROUNDS) for w in words],
    }


def _code_blob(settings: dict) -> dict:
    raw = (settings or {}).get("okbase_order_codes")
    if not isinstance(raw, dict):
        return {}
    hashes = raw.get("hashes")
    if not isinstance(hashes, list) or not raw.get("salt"):
        return {}
    return raw


def codes_left(settings: dict) -> int:
    return len(_code_blob(settings).get("hashes") or [])


def _find_code(blob: dict, code: str) -> int:
    """Where `code` sits in the blob, or -1. One derivation, then a lookup."""
    import base64
    typed = normalise_code(code)
    if not typed or not blob:
        return -1
    try:
        digest = _code_digest(typed, base64.b64decode(blob["salt"]),
                              int(blob.get("rounds") or _CODE_ROUNDS))
    except Exception:  # noqa: BLE001 - a damaged blob means "no", not a crash
        return -1
    hashes = blob.get("hashes") or []
    try:
        return hashes.index(digest)
    except ValueError:
        return -1


def code_on_list(settings: dict, code: str) -> bool:
    """Is this code usable? Spends nothing.

    Kept separate from spending so a wrong code can be turned away before the
    portal is troubled, while a right one is not struck off until the change is
    actually about to be saved.
    """
    blob = _code_blob(settings) or _code_blob(load_user_settings())
    return _find_code(blob, code) >= 0


def spend_order_code(settings: dict, code: str) -> tuple[bool, str]:
    """Use up one code. Returns (ok, reason). Never raises.

    The list is re-read from disk first and written straight back, so it stays
    right even when the caller is holding a copy made minutes ago — which the
    app's worker thread always is. `settings` is updated in place as well, so a
    caller that goes on using its own dict sees the code gone.
    """
    if not normalise_code(code):
        return False, "no ordering code was given"
    blob = _code_blob(load_user_settings()) or _code_blob(settings)
    if not blob:
        return False, ("there are no ordering codes left — make a new list in "
                       "Settings → Canteen menu → \"Ordering codes\"")
    index = _find_code(blob, code)
    if index < 0:
        return False, ("that ordering code is not on the list, or has been "
                       "used already")
    hashes = list(blob.get("hashes") or [])
    rest = dict(blob)
    rest["hashes"] = hashes[:index] + hashes[index + 1:]
    problem = save_user_settings({"okbase_order_codes": rest})
    if problem:
        # Refuse rather than proceed: a code that could not be struck off is a
        # code that would work a second time.
        return False, f"the code could not be used up ({problem})"
    settings["okbase_order_codes"] = rest
    return True, ""


def verify_password(stored: str, plain: str) -> bool:
    """Does `plain` match the stored hash? Never raises, never logs either side."""
    import base64
    import hashlib
    import hmac
    stored = str(stored or "")
    if not stored or not plain:
        return False
    try:
        kind, rounds, salt_b64, want_b64 = stored.split("$", 3)
        if kind != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"),
                                     base64.b64decode(salt_b64), int(rounds))
        # Constant-time: a length or byte difference must not be measurable.
        return hmac.compare_digest(digest, base64.b64decode(want_b64))
    except Exception:  # noqa: BLE001 - a damaged hash means "no", not a crash
        return False


def may_order(settings: dict, email: str, code: str = "") -> tuple[bool, str]:
    """May this request place orders? (allowed, reason). Spends nothing.

    **The one-time code is the protection here**, and by default the only one.
    That is a deliberate choice (the operator's, 2026-09-10): lunch gets
    ordered from more than one account — a shared `l3hapls_…` mailbox as well
    as a personal address — and a list of addresses to keep in step with that
    is a lock that mostly locks its owner out. It costs little: a code is good
    once, and it only ever appears in the chat inside the very message that
    spends it, so there is no window in which somebody could read one and use
    it.

    `okbase_order_emails` is therefore **optional** — a list, if somebody wants
    ordering pinned to particular senders, and empty (the default) meaning any
    sender who has a valid code.

    The code is checked here but **not** struck off: that happens at the last
    moment before the save (`spend_order_code`, through `change_order`'s
    `authorise` hook), so a day the portal turns out to have closed costs the
    person nothing.

    Nothing typed is ever repeated in the reason: this string goes to the chat.
    """
    allowed = settings.get("okbase_order_emails") or []
    if isinstance(allowed, str):
        allowed = [allowed]
    allowed = [str(a).strip().lower() for a in allowed if str(a).strip()]
    seen = (email or "").strip().lower()
    if allowed and seen not in allowed:
        # Only when a list was actually asked for. The address that arrived is
        # named, because "you are not allowed" is unhelpable when the truth is
        # "that is your other address" — and nothing is given away: the
        # sender's own address is on the message they just posted.
        return False, (f"ordering is limited to certain senders and "
                       f"`{seen or 'no address at all'}` is not one of them — "
                       "Settings → Canteen menu → \"Restrict ordering to\"")

    if not codes_left(settings) and not codes_left(load_user_settings()):
        return False, ("there are no ordering codes — make a list in Settings "
                       "→ Canteen menu → \"Ordering codes\" first")
    if not normalise_code(code):
        return False, ("that needs a one-time ordering code: add "
                       "`pin:yourcode` to the command")
    if not code_on_list(settings, code):
        return False, ("that ordering code is not on the list, or has been "
                       "used already")
    return True, ""


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
                filter_used: Any = None,
                states: Optional[dict[str, str]] = None,
                orders: Optional[dict[str, "Order"]] = None) -> dict:
    now = now or datetime.now()
    cache = {
        "version": 1,
        "source": "okbase",
        "fetched": now.isoformat(timespec="seconds"),
        "filter_used": filter_used,
        "days": {day: [m.as_dict() for m in meals]
                 for day, meals in sorted(days.items())},
    }
    if states:
        cache["states"] = {day: states[day] for day in sorted(states)}
    if orders:
        # Only the days that actually hold an order, and only the fields the
        # reply needs. The state word is kept because "ordered" and "already
        # collected" are different answers to "what do I have on Thursday".
        cache["orders"] = {
            day: {"items": list(o.item_ids), "state": o.state}
            for day, o in sorted(orders.items()) if o.ordered}
    return cache


def cache_states(cache: dict) -> dict[str, str]:
    raw = (cache or {}).get("states") or {}
    return {str(k): str(v) for k, v in raw.items()} \
        if isinstance(raw, dict) else {}


def cache_orders(cache: dict) -> dict[str, dict]:
    raw = (cache or {}).get("orders") or {}
    return {str(k): v for k, v in raw.items()
            if isinstance(v, dict)} if isinstance(raw, dict) else {}


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
        r = session.get(f"{base}{ALIVE_PATH}", timeout=timeout)
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


def fetch_raw(session, day_from: date, day_to: date, base: str = BASE_DEFAULT,
              timeout: float = TIMEOUT_DEFAULT, canteen_id: Any = None,
              known_filter: Any = None, user_id: Any = None,
              week_of: Optional[date] = None):
    """The whole `nacti-vse` answer, plus the body that got it.

    Returns (payload, body_used, "") or (None, None, reason). Split out of
    `fetch_menu` because ordering needs three things from the SAME answer — the
    menu, each day's open/closed state and the person's own orders — and asking
    three times would be three chances to read a different week.
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
            return None, None, str(exc)
        if not (200 <= r.status_code < 300):
            last = f"HTTP {r.status_code} {(r.text or '')[:120]}".strip()
            if r.status_code in (401, 403):
                # Not a wrong body — a signed-out session, which refuses all five
                # of them identically. Trying the rest turns one failure into five
                # and one wait into five.
                return None, None, last
            continue
        try:
            payload = r.json()
        except ValueError:
            last = "the portal did not answer with JSON"
            continue
        if parse_menu(payload):
            return payload, body, ""
        last = "the portal answered, but no meals were found in the answer"
    return None, None, last


def fetch_menu(session, day_from: date, day_to: date, base: str = BASE_DEFAULT,
               timeout: float = TIMEOUT_DEFAULT, canteen_id: Any = None,
               known_filter: Any = None, user_id: Any = None,
               week_of: Optional[date] = None):
    """Fetch the meal list for a date range.

    Returns (days, filter_used, "") on success, or ({}, None, reason).
    """
    payload, body, err = fetch_raw(session, day_from, day_to, base, timeout,
                                   canteen_id, known_filter, user_id, week_of)
    if payload is None:
        return {}, None, err
    return parse_menu(payload), body, ""


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
    days, _states, _orders, used, err = fetch_weeks_full(
        session, first_monday, weeks, base, timeout, canteen_id,
        known_filter, user_id)
    return days, used, err


def fetch_weeks_full(session, first_monday: date, weeks: int = FETCH_WEEKS,
                     base: str = BASE_DEFAULT, timeout: float = TIMEOUT_DEFAULT,
                     canteen_id: Any = None, known_filter: Any = None,
                     user_id: Any = None):
    """`fetch_weeks`, but also each day's state and this person's own orders.

    Returns (days, states, orders, filter_used, error). All three come out of
    the same answers, so this costs exactly what fetching the menu alone cost.
    """
    merged: dict[str, list[Meal]] = {}
    states: dict[str, str] = {}
    orders: dict[str, Order] = {}
    used: Any = None
    problems: list[str] = []
    for index in range(max(1, weeks)):
        monday = first_monday + timedelta(weeks=index)
        payload, body, err = fetch_raw(
            session, monday - timedelta(days=FETCH_DAYS_BACK),
            monday + timedelta(days=FETCH_DAYS_AHEAD), base, timeout,
            canteen_id, known_filter, user_id, week_of=monday)
        if payload is not None:
            merged.update(parse_menu(payload))
            states.update(parse_day_states(payload))
            # The orders half spans the whole wide range, not just the displayed
            # week, so a later week's answer repeats earlier days. Newer wins:
            # the requests go oldest first, so a plain update is the right way
            # round — but never let an empty repeat erase a known order.
            for day, order in parse_orders(payload).items():
                if order.ordered or day not in orders:
                    orders[day] = order
            used = used or body
        elif err:
            problems.append(f"week of {monday.isoformat()}: {err}")
    if not merged:
        return {}, {}, {}, None, ("; ".join(problems)
                                  or "the portal returned no meals for any week")
    return merged, states, orders, used, ""


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

    days, states, orders, used, err = fetch_weeks_full(
        session, week_monday(now.date()), FETCH_WEEKS, base, timeout,
        settings.get("okbase_canteen_id") or None,
        settings.get("okbase_filter") or None,
        settings.get("okbase_user_id") or None)
    if not days:
        return {}, err

    cache = build_cache(days, now, used, states, orders)
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


# ---------------------------------------------------------------------------
# Ordering — the only part of this module that WRITES to the portal
# ---------------------------------------------------------------------------
#
# The portal has one save endpoint, `objednavky/uloz`, and it does not take "add
# this meal". It takes the desired state of the WHOLE displayed week, keyed by
# date, exactly as the page's own checkboxes would leave it (captured live
# 2026-09-09). Two consequences, both load-bearing:
#
#   * the current state has to be read first and sent back with the one change
#     applied — building a body from the single changed day would cancel every
#     other day of that week;
#   * a day is either ``[{"zadna": true}]`` (nothing ordered) or
#     ``[{"polozkyIdMap": {"POLEVKA": id, "HLAVNI_JIDLO": id}, …}]`` — one meal
#     per course, addressed by the menu item's id.
#
# A day that had no order before also carries ``objednavkaId: 0``; a day that
# had one carries ``zadna: false`` instead. That is what the page sends, and
# guessing differently is not worth the risk on a request that rewrites a week.

SAVE_PATH = "/rest/stravovani/objednavky/uloz"


def _order_entry(item_ids: tuple[int, ...], codes: dict[int, str],
                 existed: bool) -> tuple[dict, str]:
    """One day's entry for the save body. Returns (entry, error)."""
    if not item_ids:
        return {"zadna": True}, ""
    picked: dict[str, int] = {}
    for item in item_ids:
        code = codes.get(item, "")
        if not code:
            # Never send a body that would silently drop a meal: without its
            # course code there is no key to file it under, and the portal would
            # read the day as "that meal is gone".
            return {}, (f"the portal did not say which course meal {item} "
                        f"belongs to, so the order was not touched")
        picked[code] = item
    # Exactly the two shapes the page sends: an existing order carries
    # `zadna: false`, a brand-new one `objednavkaId: 0` and no `zadna` at all.
    entry: dict = {"polozkyIdMap": picked}
    if existed:
        entry["zadna"] = False
    else:
        entry["objednavkaId"] = 0
    return entry, ""


def desired_week(menu: dict[str, list[Meal]], orders: dict[str, Order],
                 monday: date) -> tuple[dict[str, list[dict]], str]:
    """The save body's `objednavky`: this week exactly as it stands now.

    Built from what the portal just said, so sending it back unchanged is a
    no-op. The caller then overwrites the one day it wants to change.
    """
    codes: dict[int, str] = {}
    for meals in menu.values():
        for meal in meals:
            if meal.item_id is not None and meal.code:
                codes[meal.item_id] = meal.code

    out: dict[str, list[dict]] = {}
    for index in range(7):
        day = (monday + timedelta(days=index)).isoformat()
        if day not in menu:
            continue            # no menu that day — the page does not send it
        order = orders.get(day)
        ids = order.item_ids if (order and order.ordered) else ()
        entry, err = _order_entry(ids, codes, existed=bool(ids))
        if err:
            return {}, err
        out[day] = [entry]
    return out, ""


def save_orders(session, monday: date, template: Any, desired: dict,
                base: str = BASE_DEFAULT, timeout: float = TIMEOUT_DEFAULT,
                canteen_id: Any = None, user_id: Any = None) -> str:
    """POST the week's desired state. Returns "" or a reason.

    The body is the same one `nacti-vse` accepts, with `objednavky` filled in —
    that is what the page sends, right down to the wide `datumOd`/`datumDo`.
    """
    day_from = monday - timedelta(days=FETCH_DAYS_BACK)
    day_to = monday + timedelta(days=FETCH_DAYS_AHEAD)
    if isinstance(template, dict) and template:
        body = retarget_filter(template, day_from, day_to, monday)
    else:
        body = filter_candidates(day_from, day_to, canteen_id, user_id, monday)[0]
    body = dict(body)
    body["objednavky"] = desired
    try:
        r = session.post(f"{base}{SAVE_PATH}", json=body, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    if 200 <= r.status_code < 300:
        return ""
    # Quote the portal rather than interpret it: the closing time is not fixed,
    # so a refusal here is news even when the day looked open a second ago.
    return f"the portal refused it: HTTP {r.status_code} {(r.text or '')[:200]}".strip()


@dataclass
class OrderOutcome:
    """What a `/food order` or `/food cancel` actually did."""
    day: str = ""
    ordered: tuple[Meal, ...] = ()
    was: tuple[Meal, ...] = ()
    error: str = ""
    # Set when the command asked for exactly what the day already holds.
    # Nothing was sent and no code was spent; `was` holds what is there. Its
    # own field rather than an `error`, because nothing went wrong — the caller
    # answers it with the menu, which is what a person wants next.
    already: bool = False


def _meals_by_id(menu_day: list[Meal]) -> dict[int, Meal]:
    return {m.item_id: m for m in menu_day if m.item_id is not None}


def change_order(settings: dict, day: date,
                 picks: tuple[tuple[str, int], ...] = (),
                 clear: bool = False,
                 session_out: Optional[dict] = None,
                 authorise=None) -> OrderOutcome:
    """Order the given meals for `day`, or cancel that day. Never raises.

    Reads the week, applies the one change, saves, then reads it back and
    reports what the portal ended up holding — because a save that is accepted
    and a save that took effect are not the same thing on this portal.

    `picks` are (course, number) as the person typed them, and they are resolved
    against the menu READ HERE, not against the saved copy. The saved copy may
    be hours old, and the one thing worse than "the menu has changed" is
    ordering meal 2 of a list nobody is looking at any more.

    A day that is already booked is re-ordered, course by course: the picks are
    merged onto what is there, so `soup 2` on a day holding soup 1 and main 2
    leaves the main alone, and `main 0` takes the main off. Asking for exactly
    what is already booked changes nothing and costs no code.

    `authorise` is called once, with no arguments, at the last possible moment
    — the day has been read and found open, the body is built, and the save is
    the next thing to happen — and must return (ok, reason). That is where the
    one-time code is struck off, and the position is the point: a code must not
    be spent on a day the portal was never even asked to change.
    """
    out = OrderOutcome(day=day.isoformat())
    session, base, timeout, err = open_session(settings)
    if session is None:
        out.error = err
        return out
    if session_out is not None:
        session_out["cookies"] = merged_cookie_header(
            session, secrets_resolved_cookie(settings))

    monday = week_monday(day)
    canteen = settings.get("okbase_canteen_id") or None
    user = settings.get("okbase_user_id") or None
    template = settings.get("okbase_filter") or None

    payload, body, err = fetch_raw(
        session, monday - timedelta(days=FETCH_DAYS_BACK),
        monday + timedelta(days=FETCH_DAYS_AHEAD), base, timeout,
        canteen, template, user, week_of=monday)
    if payload is None:
        out.error = err
        return out

    menu = parse_menu(payload)
    states = parse_day_states(payload)
    orders = parse_orders(payload)
    iso = day.isoformat()

    if iso not in menu:
        out.error = f"there is no menu for {iso}"
        return out
    state = states.get(iso, "")
    if state != DAY_OPEN:
        # The cutoff is nominally 10:00 but comes earlier some days, so this
        # state — read a moment ago — is the only thing that may decide.
        out.error = (f"{iso} is already closed for orders"
                     if state == DAY_CLOSED else
                     f"{iso} is not open for orders (the portal says '{state}')")
        return out

    by_id = _meals_by_id(menu[iso])
    before = orders.get(iso)
    out.was = tuple(by_id[i] for i in (before.item_ids if before and
                                       before.ordered else ()) if i in by_id)

    was_ids = tuple(before.item_ids) if before and before.ordered else ()

    item_ids: tuple[int, ...] = ()
    if not clear:
        picked, err = resolve_picks(menu[iso], picks)
        if err:
            out.error = err
            return out
        if not picked:
            out.error = "no meal was named"
            return out
        # Merge onto what is already booked, course by course. A command names
        # the courses it means and says nothing about the others, so a soup
        # nobody mentioned stays; `0` is how a course is taken off. Replacing
        # the whole day instead would drop a meal the person never mentioned.
        final: dict[str, int] = {}
        for item in was_ids:
            meal = by_id.get(item)
            if meal is None or not meal.code:
                out.error = ("the portal did not say which course the meal "
                             f"already booked on {iso} belongs to, so the "
                             "order was not touched")
                return out
            final[meal.code] = item
        for code, item in picked.items():
            if item is None:
                final.pop(code, None)
            else:
                final[code] = item
        item_ids = tuple(final.values())
        if set(item_ids) == set(was_ids):
            # Exactly what is already there. Stop before the code is spent and
            # before anything is sent: the save would be a no-op, and the
            # person is owed the plain answer rather than a spent code. The
            # caller answers with the menu.
            out.already = True
            out.ordered = out.was
            return out

    desired, err = desired_week(menu, orders, monday)
    if err:
        out.error = err
        return out

    codes = {m.item_id: m.code for m in menu[iso] if m.item_id is not None}
    entry, err = _order_entry((), codes, existed=False) if clear else \
        _order_entry(tuple(item_ids), codes,
                     existed=bool(before and before.ordered))
    if err:
        out.error = err
        return out
    desired[iso] = [entry]

    if authorise is not None:
        ok, why = authorise()
        if not ok:
            out.error = why
            return out

    err = save_orders(session, monday, body, desired, base, timeout,
                      canteen, user)
    if err:
        out.error = err
        return out

    # Read it back. `uloz` answering 200 is not proof: the portal validates the
    # whole week, and a day it quietly declines comes back unchanged.
    payload, _body, err = fetch_raw(
        session, monday - timedelta(days=FETCH_DAYS_BACK),
        monday + timedelta(days=FETCH_DAYS_AHEAD), base, timeout,
        canteen, template, user, week_of=monday)
    if payload is None:
        out.error = f"it was saved, but reading it back failed: {err}"
        return out
    after = parse_orders(payload).get(iso)
    now_ids = tuple(after.item_ids) if after and after.ordered else ()
    by_id = _meals_by_id(parse_menu(payload).get(iso) or [])
    out.ordered = tuple(by_id[i] for i in now_ids if i in by_id)

    wanted = () if clear else tuple(item_ids)
    if set(now_ids) != set(wanted):
        out.error = ("the portal accepted the change but did not keep it — "
                     f"{iso} now holds {len(now_ids)} meal(s)")
    return out


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


# What marks a meal this person has ordered, in the menu itself. In front of
# the name rather than after the price: the number and the mark then sit
# together at the start of the line, where the eye already is, and a mark at
# the end of a line that wraps on a phone ends up somewhere in the middle.
ORDERED_MARK = "✅"


def render_meals(meals: list[Meal], lang: str = "both",
                 ordered: Iterable[int] = ()) -> list[str]:
    """The meals of one day: grouped by course, numbered inside each group.

    `lang` is "cs", "en" or "both". In "both" the English name goes on its own
    indented line under the Czech one — side by side they were separated only by
    a slash, which is what made the list unreadable when half the meals have one
    language and half have two.

    `ordered` are the menu item ids this person has ordered that day; those
    meals get ORDERED_MARK. Matched on the id, never on the name: two days can
    offer the same dish, and a name is not what the portal ordered.
    """
    want = {int(i) for i in ordered if isinstance(i, int)}
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
            mark = f"{ORDERED_MARK} " if meal.item_id in want else ""
            lines.append(f"{number}) {mark}**{title}**{price}")
            if second and second != title:
                lines.append(f"    _{second}_")
    return lines


def ordered_ids(cache: dict, day: date) -> list[int]:
    """The menu item ids ordered on `day`, out of the saved copy."""
    raw = (cache_orders(cache).get(day.isoformat()) or {}).get("items") or []
    return [int(i) for i in raw if isinstance(i, int)]


def _order_note(cache: dict, day: date, meals: list[Meal]) -> str:
    """A line under the day when the marks alone cannot tell the whole story.

    Two cases the marks are silent about, and both would otherwise read as
    "nothing ordered": a saved copy made before the item ids were kept, and an
    order for a meal that is not on the menu this copy holds.
    """
    want = ordered_ids(cache, day)
    if not want:
        return ""
    known = {m.item_id for m in meals if m.item_id is not None}
    if want and not known:
        return (f"_{ORDERED_MARK} You have this day ordered, but this saved "
                "menu is too old to say which meal — `/food refresh`._")
    missing = [i for i in want if i not in known]
    if missing:
        return (f"_{ORDERED_MARK} {len(missing)} ordered item(s) are not on "
                "this saved menu._")
    return ""


def _course_name(code: str) -> str:
    return "soup" if code == "POLEVKA" else _pretty_kind(code)


# The number that names no meal. `main 0` is how a course is taken OFF a day
# without touching the other one, which is the counterpart of the merging in
# change_order: a command that says nothing about the soup leaves the soup
# alone, so there has to be a way to SAY "no soup".
DROP_NUMBER = 0


def resolve_picks(meals: list[Meal], picks: tuple[tuple[str, int], ...]):
    """(course, number) → {course: menu item id or None}. Returns (map, error).

    The numbers are the ones the menu reply printed, so this counts within a
    course exactly as `render_meals` numbers within a course. `0` is the one
    number that names nothing: it comes back as ``None``, meaning "this course
    off".
    """
    picked: dict[str, Optional[int]] = {}
    for code, number in picks:
        if number == DROP_NUMBER:
            picked[code] = None
            continue
        group = [m for m in meals if m.code == code and m.item_id is not None]
        if not group:
            return {}, f"there is no {_course_name(code)} on the menu that day"
        if not 1 <= number <= len(group):
            return {}, (f"{_course_name(code)} {number} does not exist — "
                        f"that day has {len(group)}")
        picked[code] = int(group[number - 1].item_id)
    return picked, ""


def render_order_lines(meals: tuple[Meal, ...], lang: str = "both") -> list[str]:
    """One bullet per ordered meal, with its course in front."""
    lines = []
    for meal in meals:
        title, second = meal.titles(lang)
        price = f" — {meal.price}" if meal.price else ""
        lines.append(f"- {_heading(meal.kind)}: **{title}**{price}")
        if second and second != title:
            lines.append(f"    _{second}_")
    return lines


def render_orders(cache: dict, now: Optional[datetime] = None,
                  lang: str = "both") -> str:
    """The `/food orders` reply: what this person has ordered, from the cache.

    Read from the saved copy like every other reading reply, so it answers with
    the app closed. What it cannot do is prove the copy is current — hence the
    same staleness note the menu carries.
    """
    now = now or datetime.now()
    orders = cache_orders(cache)
    days = cache_days(cache)
    today = now.date()

    lines: list[str] = ["**My lunch orders**"]
    note = _stale_note(cache, now)
    if note:
        lines.append(note)
    shown = 0
    for day in sorted(orders):
        try:
            when = date.fromisoformat(day)
        except ValueError:
            continue
        if when < today:
            continue        # yesterday's lunch is not a question anybody asks
        shown += 1
        by_id = _meals_by_id(days.get(day) or [])
        picked = tuple(by_id[i] for i in (orders[day].get("items") or [])
                       if i in by_id)
        lines.append("")
        lines.append(f"**{_day_title(when)}**")
        if picked:
            lines.extend(render_order_lines(picked, lang))
        else:
            # An order whose meals are not in the saved menu — the menu half of
            # the cache reaches two weeks, the orders half further back.
            lines.append(f"- ordered ({len(orders[day].get('items') or [])} "
                         "item(s)), but that day's menu is not in the saved copy")
    if not shown:
        lines.append("")
        lines.append("Nothing ordered from today on.")
    lines.append("")
    lines.append(_footer(cache))
    return "\n".join(lines)


def render_already_ordered(outcome: "OrderOutcome",
                           lang: str = "both") -> str:
    """"That is already what you have" — the sentence, without the menu.

    The caller adds the day's menu underneath, because that is what a person
    wants to see next and only the caller can reach it.
    """
    try:
        day = date.fromisoformat(outcome.day)
        when, short = _day_title(day), day.strftime("%d.%m.")
    except ValueError:
        when, short = outcome.day, outcome.day
    if not outcome.was:
        return (f"🍽 **{when}** — nothing is ordered for that day, and the "
                "command asked for nothing either. Nothing has been changed "
                "and no code was used.")
    lines = [f"🍽 **{when}** — that is already what you have ordered:"]
    lines.extend(render_order_lines(outcome.was, lang))
    lines.append("")
    lines.append(f"Nothing has been changed and no code was used. Order a "
                 f"different number to swap it — `/food order {short} 2 "
                 f"pin:yourcode` — or `/food cancel {short} pin:yourcode` to "
                 f"drop the day altogether.")
    return "\n".join(lines)


def render_order_outcome(outcome: "OrderOutcome", cancelled: bool = False,
                         lang: str = "both") -> str:
    """What to say after a `/food order` / `/food cancel` actually ran."""
    try:
        when = _day_title(date.fromisoformat(outcome.day))
    except ValueError:
        when = outcome.day
    if outcome.already:
        return render_already_ordered(outcome, lang)
    if outcome.error:
        return f"⚠ {when}: {outcome.error}"
    if cancelled or not outcome.ordered:
        was = ""
        if outcome.was:
            names = ", ".join(m.titles(lang)[0] for m in outcome.was)
            was = f" (was {names})"
        return f"🍽 **{when}** — lunch cancelled{was}."
    lines = [f"🍽 **{when}** — ordered:"]
    lines.extend(render_order_lines(outcome.ordered, lang))
    return "\n".join(lines)


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
        parts.extend(render_meals(meals, lang, ordered_ids(cache, day)))
        note = _order_note(cache, day, meals)
        if note:
            parts.append("")
            parts.append(note)
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
        parts.extend(render_meals(meals, lang, ordered_ids(cache, day)))
        note = _order_note(cache, day, meals)
        if note:
            parts.append(note)
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
    # The Czech names too. The replies stay English, but the words TYPED at the
    # bot are typed by Czech speakers, and the course words ("polévka",
    # "hlavní") already are Czech — accepting one and not the other is the kind
    # of half-measure somebody trips over on their first order.
    "pondeli": 0, "pondělí": 0, "po": 0,
    "utery": 1, "úterý": 1, "út": 1, "ut": 1,
    "streda": 2, "středa": 2, "st": 2,
    "ctvrtek": 3, "čtvrtek": 3, "čt": 3, "ct": 3,
    "patek": 4, "pátek": 4, "pá": 4, "pa": 4,
    "sobota": 5, "so": 5, "nedele": 6, "neděle": 6, "ne": 6,
}

# Ordering, as one block, so `/help` and `/food help` say the same thing. It is
# the only command in this program that WRITES to somebody's HR portal, and the
# words got typed wrong twice while they were one bullet among twenty — hence a
# section of its own, at the very bottom of `/help`.
ORDER_HELP = (
    "**🍽 Ordering lunch**\n"
    "\n"
    "```\n"
    "/food order friday 1 pin:bakoli\n"
    "```\n"
    "\n"
    "- **order** (or `objednej`) — may sit anywhere in the line, so "
    "`/food friday order 1 pin:…` is the same thing\n"
    "- **the day** — `friday` / `pátek`, `today`, `tomorrow`, or a date "
    "(`11.9.`)\n"
    "- **the number** — the one printed under that course in the menu. A "
    "number on its own means the **main course**\n"
    "- **`pin:`** — one of your one-time codes. Each works once and is then "
    "struck off, so the code left behind in this chat is already spent\n"
    "\n"
    "The rest of it:\n"
    "\n"
    "```\n"
    "/food order friday soup 2 main 1 pin:severu   a soup as well\n"
    "/food order friday 3 pin:tumida               swap the main course\n"
    "/food order friday soup 0 pin:hedaki          the soup off, main kept\n"
    "/food cancel friday pin:nakepy                the whole day off\n"
    "/food order friday                            just shows the list\n"
    "/food orders                                  what I have ordered\n"
    "```\n"
    "\n"
    "A day you have **already booked** is re-ordered, one course at a time: "
    "`soup 2` on a day holding soup 1 and main 2 leaves the main course "
    "alone. **`0`** is how a course comes off — `main 0` drops the main and "
    "keeps the soup. Asking for exactly what is already there changes nothing "
    "and costs no code. (`change` means the same as `order`.)\n"
    "\n"
    "`/food order friday` on its own only prints that day's list with its "
    "numbers, so it needs no code. Reading the menu never does.\n"
    "\n"
    "The codes come from Settings → Canteen menu → Ordering codes, a hundred "
    "at a time; a day that turns out to be closed costs you none. Every reply "
    "names the meal the portal ended up holding, not the one I meant to order."
)


FOOD_HELP = (
    "**/food** — the canteen menu, as read from OKbase.\n"
    "- `/food` — today until 14:30, and the next serving day after that, "
    "because by then today's lunch is over\n"
    "- `/food today` — today whatever the time is\n"
    "- `/food tomorrow` — tomorrow\n"
    "- `/food week` — this week; `/food next week` — the one after\n"
    "- `/food monday` — that weekday of this week\n"
    "- `/food 27.8.` — that date\n"
    "- `/food refresh` — the same read, answered with today\n"
    "- `/food status` — what I have saved, what I am doing, and whether the "
    "OKbase sign-in still works\n"
    "\n"
    "Every one of these reads OKbase on the spot, so it takes a second or two "
    "and what you get is what the portal holds right now. If the portal does "
    "not answer, you get the saved copy instead, with a line saying how old it "
    "is — never an old menu passed off as today's.\n"
    "\n"
    "Add `; cz` for the Czech names only, `; en` for the English ones. Without "
    "either you get both, the English one under the Czech. It combines with "
    "everything else: `/food week; en`, `/food friday; cz`.\n"
    "\n"
    # Last here as well, for the same reason it is last in /help.
    + ORDER_HELP
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
    mode: str = "day"     # day | week | refresh | status | help
                          # order | cancel | orders
    day: Optional[date] = None
    monday: Optional[date] = None
    lang: str = "both"                # cs | en | both
    error: str = ""
    # For mode "order": (course code, the number printed in that course's list).
    # Numbers are per course because that is how the menu reply numbers them —
    # soups 1, 2 and mains 1, 2, 3 — so a single flat number would name a
    # different meal than the one the person is looking at.
    picks: tuple[tuple[str, int], ...] = ()
    # The one-time code, exactly as typed. Never rendered, never logged.
    password: str = ""
    # True for `change` / `instead`. Kept only so the word is recorded: it
    # gates nothing any more, because a plain `order` on a booked day already
    # re-orders it (see change_order).
    replace: bool = False
    # True only for a bare `/food`, where no day was named. That is the one case
    # allowed to move itself on past LUNCH_OVER_AT — see next_food_day.
    default_day: bool = False


# How the one-time code is written on the command. A marked word, not a bare
# one: a code is a made-up word and could read like anything, so it has to be
# told apart by its label and taken out before any other word is looked at.
PASSWORD_PREFIXES = ("pin:", "pw:", "pass:", "password:", "heslo:", "kod:",
                     "kód:", "code:")


def _take_password(args: str) -> tuple[str, str]:
    """(the command without the password word, the password).

    Works on the RAW text: a password is case-sensitive, so this has to happen
    before the lowercasing the rest of the parser does.
    """
    kept, found = [], ""
    for word in (args or "").split():
        low = word.lower()
        hit = next((p for p in PASSWORD_PREFIXES if low.startswith(p)), "")
        if hit and not found:
            found = word[len(hit):]
        else:
            kept.append(word)
    return " ".join(kept), found


def parse_food_args(args: str, today: Optional[date] = None) -> FoodRequest:
    """Read the words after `/food`. Never raises; unknown words come back as
    an error message written for the chat."""
    # The password comes out FIRST, off the raw text, and is put back on the
    # finished request — the parser below lowercases everything, which would
    # quietly change a password with a capital letter in it.
    args, password = _take_password(args)
    req = _parse_food_words(args, today, has_code=bool(password))
    req.password = password
    return req


def _parse_food_words(args: str, today: Optional[date] = None,
                      has_code: bool = False) -> FoodRequest:
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
    # "order" and "cancel" are looked for ANYWHERE in the line, not only as the
    # first word. "/food friday cancel" is how a person writes it, and while
    # only the first word was examined that line quietly printed the menu
    # instead — the worst possible outcome, because it looks like the command
    # was understood. Same house rule as everywhere else: two independent
    # things (which day, and what to do) may be said in either order.
    if any(w in LIST_WORDS for w in words):
        return FoodRequest("orders", lang=lang)
    for index, word in enumerate(words):
        if word in ORDER_WORDS or word in REPLACE_WORDS:
            req = _parse_order_words(words[:index] + words[index + 1:],
                                     today, lang)
            req.replace = word in REPLACE_WORDS
            return req
        if word in CANCEL_WORDS:
            rest = words[:index] + words[index + 1:]
            req = _parse_order_words(rest, today, lang)
            if req.picks:
                return FoodRequest(
                    "cancel", lang=lang,
                    error="cancelling clears the whole day, so `/food cancel` "
                          "takes a day and not a meal.")
            return FoodRequest("cancel", day=req.day, lang=lang,
                               error=req.error, default_day=req.default_day)

    # No verb, but a one-time code AND a meal named. Nobody types a code to
    # read a menu, so this is an order however it was worded — and reading
    # "/food friday main 1 pin:…" as "show me Friday" is the failure that looks
    # like success: the menu comes back and nothing was ordered.
    if has_code:
        trial = _parse_order_words(words, today, lang)
        if trial.picks:
            return trial

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


# What the person is asking for. Both languages, because the words typed at the
# bot are typed by Czech speakers even though every reply is English.
LIST_WORDS = ("orders", "ordered", "mine", "objednavky", "objednávky")
ORDER_WORDS = ("order", "book", "take", "objednat", "objednej", "objednavam",
               "objednávám", "dej", "chci")
CANCEL_WORDS = ("cancel", "unbook", "drop", "none", "odhlasit", "odhlásit",
                "odhlas", "odhlaš", "zrusit", "zrušit", "zrus", "zruš",
                "nechci")
# Ordering over the top of an order you already have. These are plain synonyms
# of ORDER_WORDS — `order` on a booked day re-orders it course by course, so
# there is nothing left for a separate word to unlock. They stay because they
# are what people type, and a word the bot does not know is an error message.
REPLACE_WORDS = ("change", "replace", "instead", "swap", "zmenit", "změnit",
                 "zmen", "změň", "misto", "místo", "prehodit", "přehodit",
                 "radeji", "raději")

# Which course a number belongs to. A bare number means the main course: that
# is what almost every order is, and "/food order friday 1" has to mean
# something obvious rather than being refused.
COURSE_WORDS = {
    "soup": "POLEVKA", "soups": "POLEVKA", "polevka": "POLEVKA",
    "polévka": "POLEVKA", "polevku": "POLEVKA", "polévku": "POLEVKA",
    "p": "POLEVKA",
    "main": "HLAVNI_JIDLO", "mains": "HLAVNI_JIDLO", "meal": "HLAVNI_JIDLO",
    "hlavni": "HLAVNI_JIDLO", "hlavní": "HLAVNI_JIDLO",
    "jidlo": "HLAVNI_JIDLO", "jídlo": "HLAVNI_JIDLO", "h": "HLAVNI_JIDLO",
    "m": "HLAVNI_JIDLO",
}
_COURSE_FILLER = ("course", "dish", "chod", "and", "a", "plus", "+", "with")
DEFAULT_COURSE = "HLAVNI_JIDLO"


def _parse_order_words(words: list[str], today: date,
                       lang: str = "both") -> FoodRequest:
    """The words after `/food order`: a day, and numbered meals per course.

    The day may sit anywhere in the line, so "friday main 1" and "main 1 friday"
    are the same order — see the house rule that two independent inputs must be
    settable in any order.
    """
    day: Optional[date] = None
    offset_weeks = 0
    course = DEFAULT_COURSE
    picks: list[tuple[str, int]] = []

    for word in words:
        if word in _COURSE_FILLER:
            continue
        if word in ("next", "this", "last", "previous", "prev"):
            offset_weeks = {"next": 1, "this": 0, "last": -1,
                            "previous": -1, "prev": -1}[word]
            continue
        if word in COURSE_WORDS:
            course = COURSE_WORDS[word]
            continue
        if word.isdigit():
            picks.append((course, int(word)))
            continue
        # A number written straight onto the course ("main1", "p2").
        head = word.rstrip("0123456789")
        if head in COURSE_WORDS and head != word:
            picks.append((COURSE_WORDS[head], int(word[len(head):])))
            continue

        if word in ("today", "now"):
            day = today
        elif word == "tomorrow":
            day = today + timedelta(days=1)
        elif word in WEEKDAY_WORDS:
            day = (week_monday(today) + timedelta(weeks=offset_weeks)
                   + timedelta(days=WEEKDAY_WORDS[word]))
        else:
            parsed = _parse_day_word(word, today)
            if parsed is None:
                return FoodRequest("order", lang=lang,
                                   error=f"I don't understand '{word}'.")
            day = parsed

    if not picks:
        # A day with no meal named. Not an error worth a scolding — the menu for
        # that day, with its numbers, is the answer to "order what?", so the
        # caller shows it and says how to pick.
        return FoodRequest("order", day=day, lang=lang,
                           default_day=day is None)
    # One meal per course is all the portal holds, so a repeated course is a
    # typo worth naming rather than silently keeping the last number.
    seen: dict[str, int] = {}
    for code, number in picks:
        if code in seen and seen[code] != number:
            return FoodRequest(
                "order", lang=lang,
                error=f"two different {'soups' if code == 'POLEVKA' else 'main courses'}"
                      " were asked for; the canteen holds one of each.")
        seen[code] = number
    return FoodRequest("order", day=day, lang=lang, default_day=day is None,
                       picks=tuple(sorted(seen.items())))


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

    `refresh` is not handled here, and neither are `order` and `cancel`: only a
    process holding the credentials can do those, so the caller sees the mode
    and decides. Everything that only READS is answered here, from the cache.
    """
    now = now or datetime.now()
    req = parse_food_args(args, now.date())
    if req.error:
        return f"⚠ {req.error}\n\n{FOOD_HELP}"
    if req.mode == "help":
        return FOOD_HELP
    if req.mode == "status":
        return render_status(cache, now)
    if req.mode == "orders":
        return render_orders(cache, now, req.lang)
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
