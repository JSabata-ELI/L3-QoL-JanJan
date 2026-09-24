"""Announcer — the model, the verdicts and the config file.

No GUI toolkit is imported here, deliberately. Everything in this file can be
exercised from a headless test, and everything that decides whether something
is wrong lives here rather than in a widget: a verdict computed inside a table
cell cannot be tested, and this program's whole job is verdicts.

ONE KIND OF THING IS WATCHED
----------------------------
The old program had three kinds of condition, a nameless left-over rectangle,
and eight hard-coded value badges that could never raise the alarm — four
mechanisms for one idea. There is now one item shape with two flavours:

    kind "value"   an archived number stays inside its limits
    kind "area"    a rectangle of a screen still looks like its reference

and one switch, `fires`, that says what happens when it stops holding:

    "show"    the row and its badge go red. No window, no sound.
    "alarm"   that, plus the flashing window and the sound.

That switch is the whole of the old split between the hard-coded badges (which
could only colour themselves) and the operator's own conditions (which could
only raise the alarm). Adding a chiller and adding an alarm are now the same
action with one box ticked differently.

FOUR LIMITS, ANY OF THEM OFF
----------------------------
A value carries `lo_lo`, `lo`, `hi`, `hi_hi`. Past `lo`/`hi` is a warning; past
`lo_lo`/`hi_hi` is a trip. Any of them may be None, which means that limit is
switched off — and None is not zero. A zero limit on an energy fires the moment
the laser runs, so an empty box has to mean "don't check", never 0.
`parse_level` is the one place a box becomes a number or None.

PRESETS ARE THE SETS OF ALARMS
------------------------------
An item carries `presets`, the names of the sets it belongs to. One set is
chosen in the header and then it is the only thing on screen and the only thing
watched — the operator sets up a night shift without looking at the alarms of a
commissioning run. An item may sit in several sets; an item in none of them
belongs to the automatic set "Unassigned", so nothing is ever lost by making the
first preset. There is no item that shows up in every preset: if it has to be in
two, it is put in two.

This replaces the old `group` field, which was an AND-gate (a group fired only
once every member had tripped). That rule is gone; each item now fires on its
own verdict. An old file's group names are carried over as preset names so
nothing has to be typed again, but they no longer hold each other back.
"""

import base64
import io
import json
import os
import socket
import sys
import urllib.error
from pathlib import Path

# ── how often things are looked at ───────────────────────────────────────────
# Reading is not alerting: the numbers and the pictures are refreshed whether or
# not the alarm is armed, so the tables are never blank and the operator can set
# a limit by watching the value move. Watching only decides whether a verdict
# raises anything. Idle is slower because nobody is waiting on it.
POLL_MS_WATCHING = 500
POLL_MS_IDLE     = 2000

# Our last successful read may be old because the network went away or because a
# read wedged. That is staleness, and it is measured in ELAPSED LOCAL TIME —
# a difference, never a comparison against an archiver timestamp, because this
# PC's clock runs about 25 s ahead of the facility.
#
# Note what staleness is NOT: a value that has not changed. The archiver writes
# on change only, so a chiller holding its setpoint perfectly publishes nothing
# for minutes. That is a healthy chiller, not a dead channel.
STALE_AFTER_S = 15.0

# How long the readings have to keep failing before the circle grows a red
# exclamation mark. Five minutes: a channel that misses a read or two, or a
# minute of archiver trouble, is normal and says nothing worth interrupting the
# operator for; five minutes of it means the readings are not coming and the
# alarm cannot see what it is watching.
#
# This mark is the ONLY thing said about unreadable data on the screen while
# watching. The sentences beside the circle are for values out of range, never
# for "not read yet" — asked for on 2026-09-21: the panel over the operator's
# work was a column of read failures, which is noise, not news.
NO_DATA_S = 300.0

# ── values ───────────────────────────────────────────────────────────────────
READ_MODES = ("peak", "mean", "last")
# "peak"  the worst sample of the window. One shot over the limit is the whole
#         event, and averaging a minute of shots would hide it.
# "mean"  the average of the newest few samples — steadier, and what the eight
#         badges have always done. A single chiller sample crosses ±0.3
#         constantly; the average does not.
# "last"  the newest sample however old it is, found with a widening look-back.
#         For a channel written on change only: a setpoint, a state, a valve.
MEAN_COUNT       = 25     # how many of the newest samples "mean" averages
DEFAULT_WINDOW_S = 10     # how far back "peak" looks
DEFAULT_PV       = "L3-PM03-023:Energy"    # back-reflection energy

# ── areas ────────────────────────────────────────────────────────────────────
DEFAULT_THRESHOLD = 1.0      # average pixel deviation, 0-255
# A reference picture lives inside the config file as base64 PNG, so a whole
# screen is refused rather than written as a megabyte of text.
MAX_REF_BYTES = 1_000_000

# ── the item ─────────────────────────────────────────────────────────────────
FIRES = ("show", "alarm")

# Verdicts, worst last. `rank` orders them for the "worst first" sort.
STATE_OFF     = "off"        # switched off by the operator
STATE_UNKNOWN = "unknown"    # could not be read, or nothing archived at all
STATE_STALE   = "stale"      # last good read is too old — we are not refreshing
STATE_OK      = "ok"
STATE_WARN    = "warn"
STATE_TRIP    = "trip"
_STATE_RANK = {STATE_OFF: 0, STATE_OK: 1, STATE_UNKNOWN: 2,
               STATE_STALE: 3, STATE_WARN: 4, STATE_TRIP: 5}


def state_rank(state):
    """How bad a verdict is, for sorting the worst to the top."""
    return _STATE_RANK.get(state, 0)


def parse_level(text):
    """A limit box: the number in it, or None when it is empty (limit off).

    Empty is not zero. A comma is accepted as the decimal point, because that is
    what a Czech keyboard produces.
    """
    if text is None:
        return None
    s = str(text).strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def level_text(value):
    """A limit as it belongs in a box: the number, or empty when it is off."""
    return "" if value is None else f"{value:g}"


# ─────────────────────────────────────────────────────────────────────────────
# Items
# ─────────────────────────────────────────────────────────────────────────────

def clean_presets(names):
    """A tidy list of preset names: trimmed, no blanks, no repeats, in order.

    Kept in one place because the membership arrives from four directions — the
    editor's tick boxes, the Assign button, the migration and the file itself —
    and a stray "  Night" would quietly become a second preset.
    """
    out = []
    for n in (names or []):
        n = str(n).strip()
        if n and n not in out:
            out.append(n)
    return out


def new_value_item(item_id, name="", pv=DEFAULT_PV, *, minus=None, unit="",
                   lo_lo=None, lo=None, hi=None, hi_hi=None,
                   read="peak", window_s=DEFAULT_WINDOW_S,
                   fires="alarm", on=True, presets=None, message=""):
    return {"id": int(item_id), "kind": "value", "name": name, "on": bool(on),
            "fires": fires if fires in FIRES else "alarm",
            "presets": clean_presets(presets),
            "message": message,
            "pv": pv, "minus": minus, "unit": unit,
            "lo_lo": lo_lo, "lo": lo, "hi": hi, "hi_hi": hi_hi,
            "read": read if read in READ_MODES else "peak",
            "window_s": float(window_s)}


def new_area_item(item_id, name="", *, monitor=0, screen="", region=None,
                  threshold=DEFAULT_THRESHOLD, reference=None,
                  fires="alarm", on=True, presets=None, message=""):
    return {"id": int(item_id), "kind": "area", "name": name, "on": bool(on),
            "fires": fires if fires in FIRES else "alarm",
            "presets": clean_presets(presets),
            "message": message,
            "monitor": int(monitor), "screen": screen,
            "region": list(region) if region else None,
            "threshold": float(threshold), "reference": reference}


def channel_label(item):
    """What a value row watches, in words.

    A difference pair is shown as a subtraction rather than as a made-up key, so
    a chiller row says what it actually measures.
    """
    pv = item.get("pv") or ""
    minus = item.get("minus")
    return f"{pv} − {minus}" if minus else pv


def pv_names(item):
    """Every channel one value item has to read, in order."""
    out = [item.get("pv")] if item.get("pv") else []
    if item.get("minus"):
        out.append(item["minus"])
    return out


def item_summary(item):
    """One line saying what this item watches — for a table cell and the log."""
    if item.get("kind") == "area":
        r = item.get("region")
        if not r:
            return "area not drawn yet"
        w, h = int(r[2]) - int(r[0]), int(r[3]) - int(r[1])
        ref = "reference taken" if item.get("reference") else "NO REFERENCE"
        return (f"Monitor {int(item.get('monitor', 0)) + 1} · {w}x{h} px · "
                f"change over {float(item.get('threshold', DEFAULT_THRESHOLD)):.1f} · {ref}")
    limits = " ".join(f"{k} {level_text(item.get(k))}"
                      for k in ("lo_lo", "lo", "hi", "hi_hi")
                      if item.get(k) is not None) or "no limit set — it cannot fire"
    unit = item.get("unit") or ""
    return f"{channel_label(item)} · {limits}{(' ' + unit) if unit else ''}"


def search_channels(names, query, limit=400):
    """Find channel names the way an operator types: (matches, total).

    The rule, which is the same one the other programs here follow:

    * the query is split into WORDS, and every word has to appear somewhere in
      the name. Nothing has to be at the start, and nothing has to be adjacent
      — "chl temp" finds `L3-UTIL-CHL03-001:Temp`;
    * the ORDER is honoured: a name carrying the words in the order they were
      typed ranks above one carrying them jumbled;
    * a fragment that fits several channels returns THOSE channels. It is a
      filter, never an error and never the whole list back.

    `total` is how many matched before `limit` cut the list, so the caller can
    say "showing 400 of 1620" rather than silently showing part of an answer.
    """
    words = [w for w in (query or "").lower().split() if w]
    if not words:
        return [], 0
    scored = []
    for name in names:
        low = name.lower()
        if any(w not in low for w in words):
            continue
        # In order, and how early it starts: both cheap, and together they put
        # the obvious answer first.
        pos, in_order, at = 0, True, []
        for w in words:
            found = low.find(w, pos)
            if found < 0:
                in_order = False
                found = low.find(w)
            at.append(found)
            pos = max(pos, found + len(w))
        score = (0 if in_order else 1, min(at), len(name), name)
        scored.append((score, name))
    scored.sort()
    return [n for _s, n in scored[:limit]], len(scored)


def short_summary(item):
    """What this item watches, in as few words as fit a table column.

    The limits are deliberately NOT in here. On the operating screen the column
    next to it already shows the reading and the one after it the verdict, so
    repeating the limits in the middle is three ways of saying one thing. They
    are in the tooltip, and they are editable in the Values tab.
    """
    if item.get("kind") == "area":
        r = item.get("region")
        if not r:
            return "area not drawn yet"
        w, h = int(r[2]) - int(r[0]), int(r[3]) - int(r[1])
        return f"Monitor {int(item.get('monitor', 0)) + 1} · {w}x{h} px"
    return channel_label(item)


def enabled_items(items, kind=None):
    return [it for it in items
            if it.get("on") and (kind is None or it.get("kind") == kind)]


def next_id(items):
    """A fresh item id. Ids are SAVED and never reused.

    The old program keyed a condition's badge on a counter that restarted every
    run, and a badge for one of the eight values on its index in a hard-coded
    table. Neither survived an edit: editing a condition replaced the dict and
    the badge went with it. A saved id is what lets per-item state, the badges
    and the preset membership all point at the same row across an edit.
    """
    return max([int(it.get("id", 0)) for it in items] or [0]) + 1


def drop_items(items, doomed):
    """Take `doomed` out of `items`, in place. Answers how many went.

    By ID, and never `list.remove`. An item is a plain dict, so `remove` finds
    the first one that is EQUAL — and two rows watching the same thing with the
    same limits are equal. It would have taken the wrong row off the list and
    left the selected one there, which reads as "Remove did nothing". The id is
    the only thing that identifies a row; see `next_id`.

    In place, because the list belongs to the window and everything else —
    the engine, the three tables — is looking at that same list.
    """
    ids = {int(it["id"]) for it in doomed if it.get("id") is not None}
    if not ids:
        return 0
    before = len(items)
    items[:] = [it for it in items if int(it.get("id", -1)) not in ids]
    return before - len(items)


# ─────────────────────────────────────────────────────────────────────────────
# The verdicts — pure, so testing/ can walk every combination
# ─────────────────────────────────────────────────────────────────────────────

def judge_value(item, value, *, error=None, samples=0, age_s=None):
    """What one value reading means: (state, sentence).

    `value` is the number the reduction produced, or None. `error` is set only
    when the read FAILED — an empty window is not an error, and the difference
    matters: "nothing was archived" and "we could not ask" are two different
    things to tell an operator. `age_s` is how long ago our last GOOD read was,
    in elapsed local seconds.

    A reading that could not be made is never "ok". Reporting fine on the
    strength of a reading that was never taken is the one wrong answer.
    """
    if not item.get("on"):
        return STATE_OFF, "switched off"
    if error:
        return STATE_UNKNOWN, error
    if value is None:
        if samples == 0:
            return STATE_UNKNOWN, "nothing archived in the window"
        return STATE_UNKNOWN, "no value"
    if age_s is not None and age_s > STALE_AFTER_S:
        return STATE_STALE, f"not refreshed for {age_s:.0f} s — last value {value:g}"

    unit = item.get("unit") or ""
    suffix = (" " + unit) if unit else ""
    now = f"{value:g}{suffix}"
    lo_lo, lo = item.get("lo_lo"), item.get("lo")
    hi, hi_hi = item.get("hi"), item.get("hi_hi")
    # Trips outrank warnings, and the low side is checked before the high side
    # only because one of them has to go first — a value cannot breach both.
    if hi_hi is not None and value > hi_hi:
        return STATE_TRIP, f"{now} is over {hi_hi:g}{suffix}"
    if lo_lo is not None and value < lo_lo:
        return STATE_TRIP, f"{now} is under {lo_lo:g}{suffix}"
    if hi is not None and value > hi:
        return STATE_WARN, f"{now} is over {hi:g}{suffix}"
    if lo is not None and value < lo:
        return STATE_WARN, f"{now} is under {lo:g}{suffix}"
    return STATE_OK, now


def judge_area(item, diff, *, error=None, size_now=None, size_ref=None,
               age_s=None):
    """What one screen-area comparison means: (state, sentence).

    A grab whose size no longer matches the reference is a TRIP, not a shrug.
    The screen resolution or its scaling changed underneath the rectangle, the
    two pictures cannot be compared at all, and "everything is fine" would be
    the one wrong answer — so it says what changed and fires.
    """
    if not item.get("on"):
        return STATE_OFF, "switched off"
    if not item.get("region"):
        return STATE_UNKNOWN, "area not drawn yet"
    if not item.get("reference"):
        return STATE_UNKNOWN, "no reference taken — it cannot fire"
    if error:
        return STATE_UNKNOWN, error
    if size_now and size_ref and tuple(size_now) != tuple(size_ref):
        return STATE_TRIP, (f"area is now {size_now[0]}x{size_now[1]} px, "
                            f"reference {size_ref[0]}x{size_ref[1]}")
    if diff is None:
        return STATE_UNKNOWN, "could not be compared"
    if age_s is not None and age_s > STALE_AFTER_S:
        return STATE_STALE, f"not refreshed for {age_s:.0f} s"
    threshold = float(item.get("threshold", DEFAULT_THRESHOLD))
    if diff > threshold:
        return STATE_TRIP, f"picture changed (difference {diff:.1f} over {threshold:.1f})"
    return STATE_OK, f"difference {diff:.1f}"


def firing_ids(items, states):
    """Which items actually fire: every switched-on item whose verdict is a trip.

    `states` maps an item id to its verdict. There is no longer any waiting for
    a second item — the old group AND-gate was dropped when presets took over,
    so what is wrong says so the moment it is wrong.

    `items` is expected to be the chosen preset already; the caller decides what
    is being watched, this decides what fires.
    """
    return {int(it["id"]) for it in items
            if it.get("on") and states.get(int(it["id"])) == STATE_TRIP}


def fire_sentence(item, detail):
    """What the operator reads when something fires: their own words first."""
    return item.get("message") or f"{item.get('name') or 'Something'} needs attention"


# ─────────────────────────────────────────────────────────────────────────────
# Reference pictures
# ─────────────────────────────────────────────────────────────────────────────

def decode_reference(text):
    """A stored reference picture as a PIL image, or None.

    The `.copy()` is load-bearing: PIL reads lazily, and the buffer it would
    read from is gone as soon as this function returns.
    """
    if not text:
        return None
    try:
        from PIL import Image
        return Image.open(io.BytesIO(base64.b64decode(text))).copy()
    except Exception:
        return None


def encode_reference(img):
    """A PIL image as base64 PNG for the config file: (text, error).

    Refuses anything over MAX_REF_BYTES with a sentence instead of writing a
    megabyte of base64 into the settings. The caller keeps whatever reference it
    already had — "too big" is a refusal, not a reset.
    """
    try:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        raw = buf.getvalue()
    except Exception as exc:
        return None, f"Reference could not be encoded: {exc}"
    if len(raw) > MAX_REF_BYTES:
        return None, (f"Reference too big ({len(raw) // 1024} kB) — "
                      f"watch a smaller area")
    return base64.b64encode(raw).decode("ascii"), None


# ─────────────────────────────────────────────────────────────────────────────
# Readable failures
# ─────────────────────────────────────────────────────────────────────────────
# A raw traceback in a log window tells an operator nothing. Every failure that
# can reach the screen is turned into a sentence plus a hint that says what to
# try. Carried over unchanged from the tkinter version, which is the only place
# in the repo where the archiver's failures are written in plain language.

_HTTP_MESSAGES = {
    400: ("Channel name refused",
          "The archiver would not accept the request. Measured 2026-09-16: this is what a "
          "channel name it does not know comes back as — so check the spelling first. "
          "It is not a network problem; the archiver answered."),
    401: ("Unauthorized",
          "The archiver wants credentials we didn't provide."),
    403: ("Access denied",
          "The archiver refused the request — you may not be allowed to read this channel."),
    404: ("Channel not found",
          "The archiver doesn't know this PV name. It may be misspelled or simply not archived."),
    500: ("Archiver server error",
          "The archiver was reached and answered, but crashed internally while handling the request. "
          "A server-side problem — usually temporary, not your network. A query covering a long span "
          "of a fast channel is refused this way, so keep the window short."),
    502: ("Archiver gateway error",
          "A gateway in front of the archiver got a broken reply from it."),
    503: ("Archiver unavailable",
          "The archiver is temporarily down, overloaded, or restarting. Usually clears up on its own."),
    504: ("Archiver gateway timeout",
          "A gateway forwarded the request but the archiver behind it never replied in time."),
}

_NETWORK_HINTS = {
    "Connection timed out":
        "The request left your PC but no reply came back within the time limit. The archiver is "
        "reachable in principle but too slow, overloaded, or the network is congested. "
        "(Compare: 'failed' = couldn't even start the connection.)",
    "Archiver unreachable":
        "We couldn't open a connection to the archiver at all — server is down, the address is wrong, "
        "or you're not on the right network/VPN. Nothing was sent.",
    "Connection failed":
        "The connection to the archiver was actively refused or dropped mid-way. The server "
        "(or a firewall) said 'no' rather than just staying silent.",
    "Invalid server response":
        "The archiver answered, but the data wasn't readable (not valid JSON). The server may be "
        "returning an error page instead of data.",
    "Archiver too busy":
        "Every connection to the archiver was already in use, so this read never left the PC. "
        "Nothing is wrong with the channel — the next pass will read it.",
}


def _from_archiver_message(text):
    """Translate `ann_cpva`'s own failure text: (detail, hint) or None.

    The archiver client raises one exception type carrying a short message, so
    the message is what has to be read. Matched here rather than by importing
    the client, which keeps this module free of it — and free of the whole HTTP
    stack when a test only wants the verdicts.
    """
    t = (text or "").strip()
    parts = t.split()
    # Exactly the client's own "HTTP 500" shape. urllib spells its own error
    # "HTTP Error 500: ..." and must be left to the branch below, which knows
    # the exception type rather than guessing from the words.
    if len(parts) >= 2 and parts[0] == "HTTP" and parts[1].isdigit():
        code = int(parts[1])
        label, hint = _HTTP_MESSAGES.get(
            code, ("Server error", "The archiver returned an unexpected error code."))
        return f"{label} (HTTP {code})", hint
    if t.startswith("timeout after"):
        return "Connection timed out", _NETWORK_HINTS["Connection timed out"]
    if "already in use" in t:
        return "Archiver too busy", _NETWORK_HINTS["Archiver too busy"]
    if t.startswith("not read in time"):
        return "Not read in time", _NETWORK_HINTS["Archiver too busy"]
    if t.startswith("transport error"):
        return "Connection failed", _NETWORK_HINTS["Connection failed"]
    if "bad JSON" in t:
        return "Invalid server response", _NETWORK_HINTS["Invalid server response"]
    return None


def readable_pv_error(pv_name, exc):
    """(short message, plain explanation) for one failed read."""
    from_text = _from_archiver_message(str(exc))
    if from_text is not None:
        return f"{pv_name} — {from_text[0]}", from_text[1]
    if isinstance(exc, urllib.error.HTTPError):
        label, hint = _HTTP_MESSAGES.get(
            exc.code, ("Server error", "The archiver returned an unexpected error code."))
        detail = f"{label} (HTTP {exc.code})"
    elif isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", exc)
        detail = ("Connection timed out"
                  if isinstance(reason, (socket.timeout, TimeoutError))
                  else "Archiver unreachable")
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, (socket.timeout, TimeoutError)):
        detail = "Connection timed out"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, ConnectionError):
        detail = "Connection failed"
        hint = _NETWORK_HINTS[detail]
    elif isinstance(exc, (json.JSONDecodeError, ValueError)):
        detail = "Invalid server response"
        hint = _NETWORK_HINTS[detail]
    else:
        detail = str(exc) or exc.__class__.__name__
        hint = "An unexpected error occurred while reading this PV."
    return f"{pv_name} — {detail}", hint


# ─────────────────────────────────────────────────────────────────────────────
# The config file
# ─────────────────────────────────────────────────────────────────────────────
# It stays where it has always been and keeps its name: next to the program, so
# one installation has one configuration, and `presets.json`, so the operator's
# saved rectangles, window positions, flash settings and reference pictures all
# survive this rewrite untouched.
#
# The watched items are a NEW top-level key. The old `conditions` list is read,
# migrated and then written in the new shape; the old key is left where it is,
# so the tkinter version still starts if this one ever has to be rolled back.

CONFIG_NAME = "presets.json"

# Top-level keys that are NOT a saved rectangle. Everything else at the top
# level is a preset name — which is why this set has to be exactly right: a
# migration that forgets one turns `flash_mode` into a region called
# "flash_mode".
RESERVED_KEYS = {
    "items",              # the new one: every watched thing
    "conditions",         # the old condition list, kept for a roll-back
    "pv_thresholds", "window_geometry", "image_geometry", "flash_mode",
    "image_file", "color_cycle", "flash_interval", "sound_leadin",
    "sound_device", "sound_enabled", "sound_file", "sound_freq",
    "sound_duration", "flash_color", "flash_duration", "migrated_at",
    # Both of these were missing, and the failure is the one described above:
    # the first time the operator positioned the alarm or the circle, an entry
    # called `alarm_geometry` or `hud_position` appeared in the saved-rectangles
    # list on the Areas tab. `alarm_geometry` is even a four-number list, so
    # Load accepted it and fed [x, y, w, h] in as [x1, y1, x2, y2] — and Delete
    # permanently forgot where the alarm appears.
    "alarm_geometry", "hud_position",
    # The sets of alarms and which one is chosen. Same trap as the two above:
    # without these, making a preset called "Night" would put a rectangle
    # called `alarm_presets` in the Areas drop-down.
    "alarm_presets", "active_preset",
    # Where the circle and the alarm picture sit, per preset. Same trap again.
    "placements",
}

# What the header's drop-down calls the two entries that are not a preset the
# operator made. They are not stored in `alarm_presets`; ALL is remembered as an
# empty string and UNASSIGNED under this name, which cannot collide with a real
# preset because `clean_presets` strips blanks and a name cannot hold a newline.
PRESET_ALL = ""
PRESET_UNASSIGNED = "\nunassigned"

COLOR_CYCLES = ("fixed", "rainbow_blink", "rainbow_spectrum", "rainbow_wave")


def program_dir():
    """The folder the program lives in — next to the exe once it is built.

    Every asset and the config are resolved from here, not from __file__: in a
    frozen build __file__ does not point next to the exe.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_path():
    return program_dir() / CONFIG_NAME


def load_config(path=None):
    """The whole settings document, or an empty one.

    A missing, unreadable or non-dict file gives {} rather than an exception —
    but unlike the old version this says so, through the returned problem, so a
    truncated file does not silently look like a first run.
    """
    p = Path(path) if path else config_path()
    if not p.exists():
        return {}, None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{p.name} could not be read ({exc}) — starting from defaults"
    if not isinstance(data, dict):
        return {}, f"{p.name} does not hold settings — starting from defaults"
    return data, None


def save_config(cfg, path=None):
    """Write the settings, atomically.

    The reference pictures live in here, so a half-written file costs the
    operator every rectangle they ever set up. Write a temporary file beside it
    and rename, which on Windows as on anything else either happens or does not.
    """
    p = Path(path) if path else config_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, p)
        return None
    except Exception as exc:
        try:
            tmp.unlink()
        except Exception:
            pass
        return f"Settings could not be saved: {exc}"


def preset_names(cfg):
    """The saved rectangles, by name.

    Presets are kept: the operator's rectangles from earlier versions stay where
    they are and keep working. A preset is either a bare `[x1,y1,x2,y2]` (the
    old shape) or `{"region": [...], "window_geometry": ..., ...}`.
    """
    return sorted(k for k in cfg if k not in RESERVED_KEYS)


def preset_region(cfg, name):
    """The rectangle a preset holds, whichever shape it is stored in."""
    value = cfg.get(name)
    if isinstance(value, list) and len(value) == 4:
        return [int(v) for v in value]
    if isinstance(value, dict):
        region = value.get("region")
        if isinstance(region, list) and len(region) == 4:
            return [int(v) for v in region]
    return None


def set_preset_region(cfg, name, region):
    """Save a rectangle under a name, keeping any geometries the preset holds.

    Upgrades the bare-list shape to the dict shape on the way, and refuses to
    write over a reserved key — naming a preset `conditions` used to offer to
    "overwrite" the condition list and then destroy it.
    """
    if name in RESERVED_KEYS:
        return f'"{name}" is a settings name, not a region name — pick another'
    existing = cfg.get(name)
    if isinstance(existing, dict):
        existing["region"] = list(region)
    else:
        cfg[name] = list(region)
    return None


# ── the presets: named sets of alarms ────────────────────────────────────────
#
# Two things are stored. Each item carries the names of the sets it is in, and
# the config carries the list of names on its own — otherwise a preset with
# nothing in it yet would vanish the moment it was made, which is exactly when
# it is made.

def alarm_presets(cfg):
    """Every preset name the operator has made, in the order they made them."""
    return clean_presets(cfg.get("alarm_presets"))


def add_alarm_preset(cfg, name):
    """Make a preset. Returns a complaint, or None when it was made."""
    name = str(name).strip()
    if not name:
        return "A preset needs a name."
    names = alarm_presets(cfg)
    if name in names:
        return f'There is already a preset called "{name}".'
    names.append(name)
    cfg["alarm_presets"] = names
    return None


def rename_alarm_preset(cfg, items, old, new):
    """Rename a preset and carry every alarm in it across."""
    new = str(new).strip()
    if not new:
        return "A preset needs a name."
    names = alarm_presets(cfg)
    if old not in names:
        return f'There is no preset called "{old}".'
    if new != old and new in names:
        return f'There is already a preset called "{new}".'
    cfg["alarm_presets"] = [new if n == old else n for n in names]
    rename_placements(cfg, old, new)
    for it in items:
        if old in (it.get("presets") or []):
            it["presets"] = clean_presets(
                [new if n == old else n for n in it["presets"]])
    return None


def delete_alarm_preset(cfg, items, name):
    """Forget a preset. Its alarms are kept and fall back to Unassigned."""
    cfg["alarm_presets"] = [n for n in alarm_presets(cfg) if n != name]
    drop_placements(cfg, name)
    for it in items:
        if name in (it.get("presets") or []):
            it["presets"] = [n for n in it["presets"] if n != name]


def in_preset(item, preset):
    """Is this item shown and watched under the chosen preset?"""
    if preset == PRESET_ALL:
        return True
    names = item.get("presets") or []
    if preset == PRESET_UNASSIGNED:
        return not names
    return preset in names


def items_in_preset(items, preset):
    return [it for it in items if in_preset(it, preset)]


def _register_presets(cfg, items):
    """Make sure every name an item claims is also in the list of presets.

    Without this a preset that exists only on an item — carried over from an
    old group, or typed into the file by hand — would be missing from the
    drop-down, and its alarms would be reachable from nowhere: not under their
    own preset, because it is not listed, and not under Unassigned, because
    they are assigned.
    """
    names = alarm_presets(cfg)
    for it in items:
        for n in (it.get("presets") or []):
            if n not in names:
                names.append(n)
    cfg["alarm_presets"] = names


def preset_counts(items, cfg):
    """How many alarms each preset holds, plus the unassigned ones.

    The Unassigned count is what tells the operator whether anything has been
    left behind, so it is counted the same way as a real preset rather than
    worked out in the dialog.
    """
    counts = {n: 0 for n in alarm_presets(cfg)}
    loose = 0
    for it in items:
        names = [n for n in (it.get("presets") or []) if n in counts]
        if not names:
            loose += 1
        for n in names:
            counts[n] += 1
    return counts, loose


# ── where the circle and the alarm picture sit, PER PRESET ───────────────────
#
# The two placements used to be one setting each for the whole program, which is
# wrong as soon as presets are used for different work: the operations preset
# wants the circle over one screen, the night one somewhere else entirely.
#
# So they live in `cfg["placements"]`, keyed by the preset the header is on —
# including the two that are not real presets, All (an empty name) and
# Unassigned. `presets.json` is JSON, and both of those are ordinary strings, so
# they need nothing special.
#
#   "placements": {"Operations": {"hud": [x, y], "alarm": [x, y, w, h]},
#                  "Night":      {"hud": null}}
#
# A key that is PRESENT and null means "this preset was deliberately put back to
# the default corner" — which is not the same as never having been placed, and
# is why `has_placement` asks whether the key is there rather than what is in
# it. A preset with no key at all falls back to the one place the program
# remembered before this was per preset, so nothing jumps on the first run after
# the update.

PLACEMENTS_KEY = "placements"

# What each one is: how many numbers, and the old whole-program key it falls
# back to. Those two keys are still written, so rolling back to an older build
# finds the last place used.
_PLACE_LEN = {"hud": 2, "alarm": 4}
_PLACE_LEGACY = {"hud": "hud_position", "alarm": "alarm_geometry"}


def _valid_placement(value, what):
    return (isinstance(value, list) and len(value) == _PLACE_LEN[what]
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in value))


def _placement_entry(cfg, preset, make=False):
    store = cfg.get(PLACEMENTS_KEY)
    if not isinstance(store, dict):
        if not make:
            return None
        store = cfg[PLACEMENTS_KEY] = {}
    one = store.get(str(preset))
    if not isinstance(one, dict):
        if not make:
            return None
        one = store[str(preset)] = {}
    return one


def placement(cfg, preset, what):
    """Where this preset puts it: [x, y] / [x, y, w, h], or None for default."""
    one = _placement_entry(cfg, preset)
    if one is not None and what in one:
        value = one[what]
        return [int(v) for v in value] if _valid_placement(value, what) else None
    legacy = cfg.get(_PLACE_LEGACY[what])
    return [int(v) for v in legacy] if _valid_placement(legacy, what) else None


def has_placement(cfg, preset, what):
    """Has this preset been given a place of its own — even 'the default'?"""
    one = _placement_entry(cfg, preset)
    return one is not None and what in one


def set_placement(cfg, preset, what, value):
    if not _valid_placement(list(value), what):
        return
    numbers = [int(v) for v in value]
    _placement_entry(cfg, preset, make=True)[what] = numbers
    cfg[_PLACE_LEGACY[what]] = list(numbers)


def clear_placement(cfg, preset, what):
    """Put this preset back to the default corner — and remember that it is."""
    _placement_entry(cfg, preset, make=True)[what] = None


def every_preset(cfg):
    """Every name the header's drop-down offers, All and Unassigned included."""
    return [PRESET_ALL, PRESET_UNASSIGNED] + alarm_presets(cfg)


def spread_placements(cfg, preset):
    """Give this preset's places to every preset that has none of its own.

    Returns the names filled in. A preset that has already been placed — or
    deliberately reset to the corner — is never touched, which is the whole
    point: this is for the ones still unset.
    """
    filled = []
    for name in every_preset(cfg):
        if name == preset:
            continue
        took = False
        for what in ("hud", "alarm"):
            if has_placement(cfg, name, what):
                continue
            here = placement(cfg, preset, what)
            if here is None:
                clear_placement(cfg, name, what)
            else:
                set_placement(cfg, name, what, here)
            took = True
        if took:
            filled.append(name)
    return filled


def rename_placements(cfg, old, new):
    store = cfg.get(PLACEMENTS_KEY)
    if isinstance(store, dict) and str(old) in store:
        store[str(new)] = store.pop(str(old))


def drop_placements(cfg, name):
    store = cfg.get(PLACEMENTS_KEY)
    if isinstance(store, dict):
        store.pop(str(name), None)


# ── the fourteen values every machine here has ───────────────────────────────
# These are pre-filled on a first run and then they are ORDINARY ROWS: editable,
# and deletable for good. "Restore the standard values" puts them back.
#
# Each chiller is TWO rows, because each row measures one thing. The deviation
# row answers "is it holding its setpoint"; the temperature row answers "is the
# setpoint even right". The old program packed both into one badge and had the
# absolute breach outrank the deviation, which is a rule nobody could see.
CHILLER_LABELS = ["Chiller DA1", "Chiller DA2", "Chiller DA3", "Chiller DA4",
                  "Helium Chiller", "Utility chiller"]


def default_value_items(start_id=1):
    """The standard values, as ordinary items."""
    items = []
    n = start_id
    items.append(new_value_item(
        n, "Helium volume", "L3-UTIL-HEB03-001:PressOut_PSI",
        unit="PSI", lo_lo=45.5, lo=46, hi=57, hi_hi=60,
        read="mean", fires="show",
        message="Helium volume out of range"))
    n += 1
    items.append(new_value_item(
        n, "Alpha voltage",
        "HAPLS-VOLT_IN_CGL-SEEDER_ER3_ALPHA1:SeederPZTVoltage",
        unit="V", lo_lo=1.1, lo=1.2, hi=1.9, hi_hi=2.2,
        read="mean", fires="show",
        message="Alpha seeder voltage out of range"))
    n += 1
    for i, label in enumerate(CHILLER_LABELS, start=1):
        ch = f"L3-UTIL-CHL03-{i:03d}"
        items.append(new_value_item(
            n, f"{label} — off setpoint", f"{ch}:Temp", minus=f"{ch}:TempSP",
            unit="°C", lo_lo=-0.6, lo=-0.3, hi=0.3, hi_hi=0.6,
            read="mean", fires="show",
            message=f"{label} is not holding its setpoint"))
        n += 1
        # The Utility chiller runs warm on purpose; the other five do not.
        lo_lo, hi_hi = (18.0, 22.0) if i == 6 else (7.0, 17.5)
        items.append(new_value_item(
            n, f"{label} — temperature", f"{ch}:Temp",
            unit="°C", lo_lo=lo_lo, hi_hi=hi_hi,
            read="mean", fires="show",
            message=f"{label} is at the wrong temperature"))
        n += 1
    return items


# ── migration ────────────────────────────────────────────────────────────────

def _legacy_thresholds(cfg, channel, minus=None):
    """The operator's own limits for one of the eight old badges, if any.

    `pv_thresholds` was keyed by a made-up string and went through two shapes:
    the first one only ever held the two LOW limits, under the names "orange"
    and "red". Both are honoured; anything absent keeps the table default.
    """
    key = f"{channel}-{minus}" if minus else channel
    saved = (cfg.get("pv_thresholds") or {}).get(key)
    if not isinstance(saved, dict):
        return {}
    out = {}
    for new, olds in (("lo_lo", ("lo_red", "red")), ("lo", ("lo_orange", "orange")),
                      ("hi", ("hi_orange",)), ("hi_hi", ("hi_red",))):
        for old in olds:
            if isinstance(saved.get(old), (int, float)):
                out[new] = float(saved[old])
                break
    return out


def migrate(cfg):
    """Turn whatever the config holds into the one item list: (items, notes).

    Runs once, on the first start of this version. `notes` are sentences for the
    log — every dropped or reshaped thing says so out loud, because a migration
    that quietly loses a condition is indistinguishable from a bug.
    """
    notes = []
    items = []
    carried_groups = set()
    if isinstance(cfg.get("items"), list):
        # Already migrated: take the items as they are, repairing only what
        # cannot be left broken.
        for raw in cfg["items"]:
            if not isinstance(raw, dict) or raw.get("kind") not in ("value", "area"):
                continue
            it = dict(raw)
            it["id"] = int(it.get("id") or next_id(items))
            it["on"] = bool(it.get("on", True))
            it["fires"] = it.get("fires") if it.get("fires") in FIRES else "alarm"
            # The old `group` was an AND-gate; presets took its place. The name
            # the operator typed is kept, as a preset of the same name, so a
            # pair that used to be "the chillers" is still one click away — it
            # simply no longer holds its members back.
            group = str(it.pop("group", "") or "").strip()
            it["presets"] = clean_presets((it.get("presets") or [])
                                          + ([group] if group else []))
            if group:
                carried_groups.add(group)
            it["message"] = str(it.get("message") or "")
            it["name"] = str(it.get("name") or "(unnamed)")
            if it["kind"] == "value":
                it["read"] = it.get("read") if it.get("read") in READ_MODES else "peak"
                it["window_s"] = float(it.get("window_s") or DEFAULT_WINDOW_S)
                for k in ("lo_lo", "lo", "hi", "hi_hi"):
                    v = it.get(k)
                    it[k] = float(v) if isinstance(v, (int, float)) else None
                if not (it.get("pv") or "").strip():
                    notes.append(f'"{it["name"]}" has no channel — dropped')
                    continue
            else:
                region = it.get("region")
                if region is not None and (not isinstance(region, list) or len(region) != 4):
                    notes.append(f'"{it["name"]}" had an unreadable rectangle — dropped')
                    continue
                it["threshold"] = float(it.get("threshold") or DEFAULT_THRESHOLD)
                it["monitor"] = int(it.get("monitor") or 0)
            items.append(it)
        _register_presets(cfg, items)
        if carried_groups:
            notes.append("The old groups are now presets: "
                         + ", ".join(sorted(carried_groups))
                         + ". Each alarm now fires on its own — a preset is a "
                           "set to work in, not a condition.")
        return items, notes

    # ── a first run on an older config ──────────────────────────────────────
    nid = 1
    # The eight badges become eight — now fourteen — ordinary value rows, with
    # whatever limits the operator had typed into the old PV Limits table.
    for it in default_value_items(nid):
        minus = it.get("minus")
        it.update(_legacy_thresholds(cfg, it["pv"], minus))
        items.append(it)
        nid = it["id"] + 1

    for raw in (cfg.get("conditions") or []):
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        name = str(raw.get("name") or "(unnamed)").strip() or "(unnamed)"
        on = bool(raw.get("enabled", True))
        message = str(raw.get("message") or "")
        if kind == "hall":
            notes.append(f'"{name}" watched where the beam goes — that check is gone, '
                         f'so it was dropped')
            continue
        if kind == "screen":
            region = raw.get("region")
            if not (isinstance(region, list) and len(region) == 4):
                notes.append(f'"{name}" had no readable rectangle — dropped')
                continue
            items.append(new_area_item(
                nid, name, monitor=int(raw.get("monitor") or 0),
                region=[int(v) for v in region],
                threshold=float(raw.get("threshold") or DEFAULT_THRESHOLD),
                reference=raw.get("reference"), fires="alarm", on=on,
                message=message))
            nid += 1
            continue
        if kind == "pv":
            pv = str(raw.get("pv") or "").strip()
            if not pv:
                notes.append(f'"{name}" had no channel — dropped')
                continue
            items.append(new_value_item(
                nid, name, pv, unit=str(raw.get("unit") or ""),
                hi=parse_level(raw.get("warn")), hi_hi=parse_level(raw.get("trip")),
                read="peak", window_s=DEFAULT_WINDOW_S,
                fires="alarm", on=on, message=message))
            nid += 1
            # An attached screen area was never a gate: the old code put it in
            # the same flat list of pictures that any single failure tripped, so
            # "both must hold" was already "either failing fires". Two rows say
            # the same thing and stop hiding one of them inside the other.
            gate = raw.get("gate_region")
            if isinstance(gate, list) and len(gate) == 4:
                items.append(new_area_item(
                    nid, f"{name} · area",
                    monitor=int(raw.get("gate_monitor") or 0),
                    region=[int(v) for v in gate],
                    threshold=float(raw.get("gate_threshold") or DEFAULT_THRESHOLD),
                    reference=raw.get("gate_reference"), fires="alarm", on=on,
                    message=message))
                nid += 1
                notes.append(f'"{name}" had a screen area attached — it is now a '
                             f'row of its own, "{name} · area"')
            continue
        notes.append(f'"{name}" is of an unknown kind ({kind!r}) — dropped')

    if notes:
        notes.insert(0, "Settings brought over from the previous version:")
    return items, notes
