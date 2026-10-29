"""Laser settings — the halls, the watched channels, and what counts as in range.

No Qt in here on purpose: every rule in this file is exercised from headless
tests, and none of it needs a screen.

WHAT THE PROGRAM IS ABOUT
-------------------------
Five halls — Int, E2, E3, E4, E5 — and one of them is being shot into. Whether a
hall is being shot into is read off its safety gate valve, which reports its
position on two channels:

    ..._IN_OPEN_POSITION      1 = the valve is open,   0 = it is not
    ..._IN_CLOSED_POSITION    1 = the valve is closed, 0 = it is not

ONE of them is read: the open switch. It is a complete answer on its own — a
gate valve that is not open is closed — so reading the second channel every
second would be a request a second spent on a question already answered. The
closed switch is the FALLBACK and nothing else: it is asked for only while the
open one cannot be read, and it drops back out of the pass the moment the open
one comes back. Its name is not a setting; it is in `DEFAULT_GATES` and is
changed by a rebuild.

Which position means "shooting" is a fact about the machine, not a setting, so
nobody is asked: Int is internal shooting, so the beam goes there when the
valve is CLOSED, and for the experimental halls the valve is open when the beam
goes in. Whenever one valve is open the others are shut.

There is ONE list of watched channels, shared by all five halls. What differs
per hall is the limits, and ONLY the limits: the same delay may have to sit
between 10 and 20 for E2 and between 30 and 40 for E4. A reading is in range
when

    min <= value <= max

with the boundaries themselves counted as in range, as asked for. An empty box
means that side is not limited — it never means zero, because a zero limit on a
voltage would fire the moment the laser is switched on.

`show` is NOT per hall. It is one switch on the row: off in E2 is off in Int and
E5 as well, and the row leaves the first tab altogether. Asked for explicitly —
"show funguje jako globální nastavení, pouze min a max funguje jako specifické".

WHEN NOTHING IS BEING SHOT
--------------------------
No hall shooting means no set of limits is in force, so nothing is judged: every
row keeps showing its number and wears a grey dash instead of a tick or a cross.
That is deliberate. Inventing a verdict out of the limits of a hall nobody is
shooting into would put a red cross on screen that means nothing, and after a
week of those nobody looks at the crosses at all.

There is ONE exception, and it is `halls_in_force`. When SGV502 to SGV505
cannot be read at all (until 15.10.2026 that was every day; now it means the
L3BT archive is down and the L3BT MSS window is not open), the experimental
halls can never say they are being shot into and NOTHING would ever be judged —
the program would be a wall of grey dashes. While not one of those four valves
can be read, Int's limits are in force whatever Int's own valve says, and Int's
square goes WHITE rather than green: we are watching Int, we are not claiming
the beam is in it. The rule switches itself off as soon as one of them answers.

WHAT A MISSING READING IS NOT
-----------------------------
It is not a zero and it is not a pass. A read that failed, a channel with
nothing archived, and a channel nobody has read for half a minute are all "we do
not know", drawn as a grey dash and never as a green tick.
"""

from __future__ import annotations

import json
import math
import os
import re
import socket
import sys
import time
import urllib.error
from pathlib import Path
from typing import NamedTuple

# ─────────────────────────────────────────────────────────────────────────────
# The halls
# ─────────────────────────────────────────────────────────────────────────────
# In this order, left to right, everywhere in the program: the big squares, the
# switcher on the Settings tab, and the columns of any table that lists them.
HALLS = ("Int", "E2", "E3", "E4", "E5")

# What a position switch has to say for it to count as made. Read as a number
# and compared with room to spare, because the archiver hands back a float and
# a channel that is a switch has been seen to publish 0.9999.
SWITCH_ON = 1.0
SWITCH_TOLERANCE = 0.4

OPEN = "open"
CLOSED = "closed"

# The valve position that means the beam is going into that hall. Int is
# internal shooting: the beam goes there while the gate valve is SHUT, which is
# the opposite of every other hall. This is not a setting — nobody is asked,
# because it is how the machine is built.
DEFAULT_SHOOT_WHEN = {"Int": CLOSED, "E2": OPEN, "E3": OPEN,
                      "E4": OPEN, "E5": OPEN}

# The gate valve of each hall. The names follow one pattern — SGV501 for
# internal shooting, then 502 to 505 for E2 to E5 — so all five are written in
# here rather than typed by hand later.
#
# Only the OPEN name is a setting. The CLOSED one is the fallback, it is read
# only while the open switch cannot be read, and it is deliberately not
# editable: a wrong name there would be found once a year, on the one day the
# open switch was down.
#
# SGV501 publishes on the CPVA archiver as L3-PSS:…. SGV502 to SGV505 never
# have there; they publish in the L3BT archive (read through the L3BT proxy, see
# `ls_cpva`) as L3BT-MSS:SGV50x_OPEN_IN / _CLOSED_IN — the same switches the
# L3BT MSS window draws. Checked 15.10.2026: those are live, while the E3/E4/E5-
# PSS-SGV50x:VALVE_IN_*_POSITION and L3BT-MSS:SGV50x_CLOSED names hold nothing
# but "Disconnected" rows since 13.8.2026.
_SGV = {"Int": 501, "E2": 502, "E3": 503, "E4": 504, "E5": 505}
DEFAULT_GATES = {
    hall: ({"open": f"L3-PSS:SGV{n}_IN_OPEN_POSITION",
            "closed": f"L3-PSS:SGV{n}_IN_CLOSED_POSITION"} if n == 501 else
           {"open": f"L3BT-MSS:SGV{n}_OPEN_IN",
            "closed": f"L3BT-MSS:SGV{n}_CLOSED_IN"})
    for hall, n in _SGV.items()
}

# The old default names of SGV502 to SGV505, which never published. A settings
# file still holding one of them is moved onto the new default when it is read;
# any other name the operator typed is left alone.
_DEAD_GATE_NAMES = {f"L3-PSS:SGV{n}_IN_OPEN_POSITION": hall
                    for hall, n in _SGV.items() if n != 501}


def switch_made(value):
    """True when a position switch says it is in that position."""
    if value is None:
        return False
    try:
        return abs(float(value) - SWITCH_ON) <= SWITCH_TOLERANCE
    except (TypeError, ValueError):
        return False


def switch_sense(pv):
    """Which position a switch channel reports: OPEN, CLOSED, or None.

    Read out of the NAME, because that is the only thing that says it:
    `SGV502_IN_OPEN_POSITION` reads 1 while the valve is open, and
    `SGV502_IN_CLOSED_POSITION` reads 1 while it is SHUT. The number means the
    opposite thing in the two channels, so nothing in this program may hard-wire
    "1 is open" — asked for in those words on 30.9.2026.

    None for a name that says neither. Nothing is then claimed about the
    position: the reading is shown as the bare number and the hall falls back to
    its closed switch, exactly as it does for a channel that cannot be read.

    CLOSED wins when both words are in the name, because the only names in this
    machine that carry both are of the form `..._IN_CLOSED_POSITION` sitting
    beside an `_OPEN_` sibling in some longer path.
    """
    name = (pv or "").upper()
    if "CLOS" in name:
        return CLOSED
    if "OPEN" in name:
        return OPEN
    return None


def default_gate(hall):
    """A hall's gate as a brand new settings file has it.

    One key. The closed switch is not in here because it is not a setting —
    see `closed_channel`.
    """
    return {"open": DEFAULT_GATES.get(hall, {}).get("open", "")}


def closed_channel(hall):
    """The hall's CLOSED switch: the fallback, from the code, never the file."""
    return DEFAULT_GATES.get(hall, {}).get("closed", "")


def shoot_when(hall):
    """The valve position that means the beam is going into this hall."""
    return DEFAULT_SHOOT_WHEN.get(hall, OPEN)


def gate_for(shooting, hall):
    """One hall's gate entry, filled in and written back if it was missing.

    Two older shapes are read and quietly migrated, because both are sitting in
    settings files right now:

    * a bare channel name — the shape from before the valve had two channels.
      It was the OPEN switch, so that is what it becomes;
    * an entry with `closed`, `on` or `shoot_when` in it. None of the three is
      a setting any more, and they are STRIPPED here rather than left to rot:
      a stale `shoot_when` sitting in the file would be the first thing anybody
      reaching for an explanation would believe.
    """
    entry = shooting.get(hall)
    if isinstance(entry, str):
        entry = {"open": entry.strip()}
        shooting[hall] = entry
    elif not isinstance(entry, dict):
        entry = default_gate(hall)
        shooting[hall] = entry
    entry.setdefault("open", "")
    for dead in ("closed", "on", "shoot_when"):
        entry.pop(dead, None)
    return entry


def _readable(reading, seen, now_s):
    """True when a reading can be believed: it arrived, and it is not old."""
    if reading is None or reading.error or reading.value is None:
        return False
    return seen is not None and now_s - seen <= STALE_AFTER_S


def gate_channels(shooting, hall, readings=None, last_ok=None, now_s=None):
    """The channel names of a hall's gate, in reading order.

    Normally exactly one: the open switch, which answers on its own. The closed
    switch is added only while the open one CANNOT be read — it errored, the
    archiver holds nothing for it, or nothing has arrived for `STALE_AFTER_S`.
    Before the first pass there is no reading at all, so nothing extra is asked
    for; and the moment the open switch comes back the fallback drops out of
    the pass again.

    A hall whose open name the operator cleared reads the closed one straight
    away — that is the only way left to answer at all.
    """
    open_pv = (gate_for(shooting, hall).get("open") or "").strip()
    closed_pv = (closed_channel(hall) or "").strip()
    if not open_pv:
        return [closed_pv] if closed_pv else []
    if not closed_pv or readings is None:
        return [open_pv]
    if open_pv not in readings:
        return [open_pv]        # never tried yet — do not ask for both
    now = time.time() if now_s is None else now_s
    if _readable(readings.get(open_pv), (last_ok or {}).get(open_pv), now):
        return [open_pv]
    return [open_pv, closed_pv]


# What `hall_state` can come back with, beyond a position.
STATE_SHOOTING = "shooting"
STATE_IDLE = "idle"
STATE_UNSET = "unset"        # no channel set for this hall at all


def _switch(reading, seen, now_s):
    """One position switch: True, False, or None when it cannot be trusted.

    None is not False. A switch we could not read says nothing about where the
    valve is, and the fallback switch is then the whole answer.
    """
    if not _readable(reading, seen, now_s):
        return None
    return switch_made(reading.value)


def _screen_position(screen, hall, now_s):
    """The valve position read off the L3BT MSS window, or None.

    `screen` is an `ls_screen.ScreenRead`. A look older than `STALE_AFTER_S` is
    not believed, for the same reason an old channel reading is not.
    """
    if screen is None or now_s - screen.at > STALE_AFTER_S:
        return None
    return (screen.positions or {}).get(hall)


def hall_state(shooting, hall, readings, last_ok, now_s, screen=None):
    """Is this hall being shot into? (state, sentence).

    `state` is STATE_SHOOTING, STATE_IDLE, STATE_UNSET or STATE_UNKNOWN. The
    sentence is what the square's tooltip says, and it always names the channel
    it came from.

    The switch in the box answers on its own: made means it is in the position
    its own NAME reports, not made means the other one, because a gate valve that
    is not open is shut. Which position that is comes from `switch_sense`, so
    pointing the box at an `..._IN_CLOSED_POSITION` channel is read the right way
    round rather than as the exact opposite of the truth. A name that says
    neither word is taken as an open switch, which is what the box has always
    been. Only when it cannot be read at all is the closed switch looked at, and
    the sentence then says so.

    When neither switch can be read, `screen` is the last resort: the valve's
    box on the L3BT MSS window (`ls_screen`). It is the stopgap for SGV502 to
    SGV505, which do not publish yet, and it is asked LAST, so the day they do,
    the channels answer and the picture is never looked at again.
    """
    open_pv = (gate_for(shooting, hall).get("open") or "").strip()
    closed_pv = (closed_channel(hall) or "").strip()
    seen = _screen_position(screen, hall, now_s)
    if not open_pv and not closed_pv and seen is None:
        return STATE_UNSET, "No gate valve channel has been set for this hall."

    o = _switch((readings or {}).get(open_pv),
                (last_ok or {}).get(open_pv), now_s) if open_pv else None
    if o is not None:
        made = switch_sense(open_pv) or OPEN
        other = CLOSED if made == OPEN else OPEN
        position, used, only = (made if o else other), open_pv, ""
    else:
        c = _switch((readings or {}).get(closed_pv),
                    (last_ok or {}).get(closed_pv), now_s) if closed_pv else None
        if c is not None:
            position, used = (CLOSED if c else OPEN), closed_pv
            only = ("\n(the open switch could not be read, so this is the "
                    "closed one)")
        elif seen is not None:
            position, used = seen, "Read off the L3BT MSS window"
            only = ("\n(the valve's channels do not publish, so this is the "
                    "picture on the screen)")
        else:
            lines = [p for p in (open_pv, closed_pv) if p]
            if screen is not None and screen.problem:
                lines.append(screen.problem)
            return (STATE_UNKNOWN,
                    "The gate valve position could not be read.\n"
                    + "\n".join(lines))

    word = "open" if position == OPEN else "closed"
    shown = hall_shown(hall)
    if position == shoot_when(hall):
        return (STATE_SHOOTING,
                f"The gate valve is {word}, so the beam is going into {shown}."
                f"\n{used}{only}")
    return (STATE_IDLE,
            f"The gate valve is {word}, so the beam is not going into {shown}."
            f"\n{used}{only}")


# The hall the program falls back to while the experimental halls cannot answer,
# and the four valves whose silence is what triggers it.
FALLBACK_HALL = "Int"
_FALLBACK_WATCHES = tuple(h for h in HALLS if h != FALLBACK_HALL)


def halls_in_force(shooting, readings, last_ok, now_s, screen=None):
    """Whose limits are being judged against right now: (halls, fallback).

    Normally the halls actually being shot into, which is what it always was.

    `fallback` is True in the one case the machine is in today: not one of the
    four experimental valves (SGV502 to SGV505) can be read, so no hall can
    possibly say it is being shot into, and with the old rule the whole program
    would be a wall of grey dashes for ever. Int's limits are then in force
    whatever Int's own valve says.

    What counts as "cannot answer" is `hall_state` coming back UNKNOWN or UNSET
    — no channel set, the read failed, nothing is archived, or nothing has
    arrived for `STALE_AFTER_S`. A hall that answers IDLE has answered: the
    machine is reporting, so there is nothing to guess at and the fallback stays
    out of the way. That is also how the rule switches ITSELF off the day the
    first of SGV502 to SGV505 publishes.

    It does not pretend Int is being shot into. The caller paints Int's square
    WHITE, not green — see `ls_ui.HallSquare`'s "watch" state. A square saying
    "shooting" while the valve is open would be a lie on screen.

    `screen` (`ls_screen.ScreenRead`) answers for the four valves while their
    channels are silent, which switches the fallback off for as long as the
    L3BT MSS window can be read — see `hall_state`.
    """
    states = {h: hall_state(shooting, h, readings, last_ok, now_s, screen)[0]
              for h in HALLS}
    active = [h for h in HALLS if states[h] == STATE_SHOOTING]
    if active:
        return active, False
    if any(states[h] not in (STATE_UNKNOWN, STATE_UNSET)
           for h in _FALLBACK_WATCHES):
        return [], False
    return [FALLBACK_HALL], True


def screen_wanted(shooting, readings, last_ok, now_s):
    """True while any of SGV502 to SGV505 cannot answer from its channels.

    Only then is the L3BT MSS window looked at; once all four publish, nobody
    photographs it any more.
    """
    return any(hall_state(shooting, h, readings, last_ok, now_s)[0]
               in (STATE_UNKNOWN, STATE_UNSET) for h in _FALLBACK_WATCHES)


def single_hall(shooting, readings, last_ok, now_s, screen=None):
    """The one hall the one-square mode shows.

    The beam never goes into two halls at once, and when it goes into none of
    E2 to E5 it is going into Int — said on 1.10.2026: "pokud to nejde ani do
    jedné haly, tak to určitě jde interně". So it is the hall in force, and Int
    whenever there is none.
    """
    active, _fallback = halls_in_force(shooting, readings, last_ok, now_s,
                                       screen)
    return active[0] if active else FALLBACK_HALL


# ── L3, and "waiting for hall" ───────────────────────────────────────────────
# Asked for on 2.10.2026: Int's square is called L3 — it is the laser itself,
# shooting internally while SGV501 is shut. With SGV501 OPEN the beam is let
# out towards the experimental halls, and while none of their valves is open
# (or none of them can be read) it has nowhere to go yet: the square says
# "waiting for hall". With SGV501 open and a hall's valve open, that hall's
# square lights up and L3 is plain idle.
#
# Only the name ON SCREEN changes. Since 14.10.2026 that is everywhere — the
# squares, the Limits tab's hall buttons and every sentence a person reads
# ("v limits nastavení pro Int, které se má jmenovat L3"). The hall is still
# "Int" in the file and in saved sets, so nothing saved needs converting.
HALL_SHOWN = {"Int": "L3"}
STATE_WAITING = "waiting"


def hall_shown(hall):
    """The name a hall's square carries: L3 for Int, the hall's own otherwise."""
    return HALL_SHOWN.get(hall, hall)


def l3_waiting(shooting, readings, last_ok, now_s, screen=None):
    """(True / False, sentence): is L3 waiting for a hall?

    True when SGV501 reads OPEN and no experimental hall is being shot into —
    their valves closed or unreadable. False whenever SGV501 itself is shut or
    cannot be read; the square then shows what it always did.
    """
    state, _why = hall_state(shooting, FALLBACK_HALL, readings, last_ok, now_s,
                             screen)
    if state != STATE_IDLE:
        return False, ""
    unread = []
    for hall in _FALLBACK_WATCHES:
        s = hall_state(shooting, hall, readings, last_ok, now_s, screen)[0]
        if s == STATE_SHOOTING:
            return False, ""
        if s in (STATE_UNKNOWN, STATE_UNSET):
            unread.append(hall)
    text = ("SGV501 is open, so the beam leaves L3 — but no hall's gate valve "
            "is open yet.")
    if unread:
        text += (f"\n({', '.join(unread)}: the valve could not be read, so "
                 f"it is not known to be open.)")
    return True, text


# ── is the laser actually firing? ────────────────────────────────────────────
# The gate valves only say where the beam WOULD go. SGV501 shut means the path
# leads into Int, and that is true all evening with nobody firing — reported on
# 1.10.2026, "reálně nestřílíme" over a green "Int SHOOTING". The operator's
# rule, the same day: "když se střílí, tak je energie aspoň 0,1 J" — SBW4's raw
# energy, not the compressed figure. A hall whose valve points at it while the
# energy is under that is WHITE "idle"; its limits are still judged (the rows
# keep their ticks and crosses), but the square neither goes green nor blinks.
#
# SBW4 is archived under two names that take turns, so both are read and the
# NEWER sample answers (the HAPLS name on a tie — it is the one searched first
# everywhere else). Newer, and not "the first one that holds anything": the
# reader looks back up to a year, so the name not in use still hands back its
# last value from weeks ago, and that value may well have been a shot.
FIRING_CHANNELS = ("HAPLS-ENER_IN_SBW4_LT5_DIAG2:Energy",
                   "L3-SBW4-PM311:Energy")
FIRING_MIN_J = 0.1

# A shot is SHOOTING for this long after its energy arrived, and then idle until
# the next one. Asked for on 2.10.2026: "Shooting status se bude ukazovat
# maximálně 15 s po tom, co se aktualizuje poslední výstřel". The archiver
# writes on change only, so without this the last shot of the evening stayed
# "at least 0.1 J" — and green — all night.
FIRING_HOLD_S = 15.0

# ── a trigger is not low power ──────────────────────────────────────────────
# Reported on 15.10.2026: "když střílíme HP a pak najednou přijde tzv. trigger
# shot …, tak začne program blikat, protože jakoby si myslí, že jedeme low
# power". A trigger is one or two shots with no energy, far apart; low power
# shooting is a STREAM of them ("v LP ty shoty chodí rychle za sebou bez
# energie", about 3.3 Hz). Read off SBW4's archive, 5.–15.10.2026:
#
#   high power             0.1–0.2 samples a second, all over 0.05 J
#   a trigger in it        1–3 samples of 0.016–0.021 J between the HP ones
#   low power shooting     about 1.1–1.25 samples a second, 0.016–0.021 J,
#                          for hours (the archiver drops repeats, so not 3.3)
#
# So low power is LP_STREAM_N samples at or under DM_SPLIT_J within
# LP_STREAM_S of the newest one — five in ten seconds is four seconds of a
# real stream, and at HP's pace even a trigger between every pair of shots
# makes two. A lone low sample decides nothing: it neither says low power nor
# cancels the high power shot before it, and the hall stays SHOOTING.
#
# The SAST1 shutter on its own says low power (`dm_judge`) only once no HP shot
# has come for HP_HOLD_S: the shutter stays open between the shots of a high
# power sequence, and the gap after a single shot can be long. After that, a
# low power stream still says so at once — the switch to low power took 20–30 s
# or more (15.10.2026).
LP_STREAM_N = 5
LP_STREAM_S = 10.0
HP_HOLD_S = 120.0


def _newest_energy(readings, last_ok, now_s):
    """(newest_ns, pv, value) of the newer readable SBW4 name, or None."""
    best = None
    for pv in FIRING_CHANNELS:
        reading = (readings or {}).get(pv)
        if not _readable(reading, (last_ok or {}).get(pv), now_s):
            continue
        t = getattr(reading, "newest_ns", None) or 0
        if best is None or t > best[0]:
            best = (t, pv, float(reading.value))
    return best


def firing(readings, last_ok, now_s):
    """(True / False / None, sentence): is the newest shot's energy a shot?

    Only the VALUE half of the rule — how long ago it came is `FiringWatch`'s.
    None when neither energy name could be read. Nothing is claimed then, and
    the caller treats it as not firing: a green square has to be earned.
    """
    best = _newest_energy(readings, last_ok, now_s)
    if best is None:
        return (None, "The SBW4 energy could not be read, so the laser is not "
                      "claimed to be firing.")
    _t, pv, value = best
    on = value >= FIRING_MIN_J
    return on, (f"SBW4 energy {value:.3f} J — {'at least' if on else 'under'} "
                f"{FIRING_MIN_J:g} J, so the laser is "
                f"{'' if on else 'not '}firing.\n{pv}")


def _samples_of(readings, pv):
    """[(t_ns, J)] of one SBW4 name's last samples, oldest first: the recent
    ones when the reading carries them, else just the newest."""
    reading = readings[pv]
    stamps = tuple(getattr(reading, "recent_ns", ()) or ())
    values = tuple(getattr(reading, "recent_v", ()) or ())
    out = []
    for t, v in zip(stamps, values):
        try:
            out.append((int(t), float(v)))
        except (TypeError, ValueError):
            continue
    newest = getattr(reading, "newest_ns", None) or 0
    if not out or out[-1][0] < newest:
        out.append((int(newest), float(reading.value)))
    return out


class FiringWatch:
    """Is the laser firing NOW, and at which power: SBW4's samples, and when
    they arrived.

    A new sample is noticed by its archiver timestamp, and WHEN it was noticed
    is taken off this PC's monotonic clock — never by comparing the sample's
    own stamp with `time.time()`: this PC runs about 25 s ahead of the
    archiver, so every shot would look too old.

    The samples already in the archive when the program starts do not count.
    Nobody knows when they were fired, and the square waits for the next one.

    Every new sample is looked at, not just the newest: a trigger landing in
    the same pass as a high power shot must not hide it (`LP_STREAM_N`).
    """

    def __init__(self, hold_s=FIRING_HOLD_S):
        self.hold_s = float(hold_s)
        self._last_ns = None        # newest stamp already looked at
        self._fire = None           # (monotonic seen, J) of the last >= 0.1 J
        self._high = None           # (monotonic seen, J) of the last HP shot
        self._stream = None         # (monotonic seen, J, count) of LP

    def _look(self, readings, last_ok, now_s, mono_s):
        best = _newest_energy(readings, last_ok, now_s)
        if best is None:
            return
        samples = _samples_of(readings, best[1])
        top = samples[-1][0]
        if self._last_ns is None:
            self._last_ns = top             # history, not a shot
            return
        new = [(t, v) for t, v in samples if t > self._last_ns]
        if not new:
            return
        self._last_ns = top
        mono = float(mono_s)
        for _t, v in new:
            if v >= FIRING_MIN_J:
                self._fire = (mono, v)
            if v > DM_SPLIT_J:
                self._high = (mono, v)
        # A stream of low shots: LP_STREAM_N of them within LP_STREAM_S of
        # the newest, which has to be one of them and has to be new.
        if samples[-1][1] <= DM_SPLIT_J:
            cut = top - int(LP_STREAM_S * 1e9)
            lows = sum(1 for t, v in samples if t >= cut and v <= DM_SPLIT_J)
            if lows >= LP_STREAM_N:
                self._stream = (mono, samples[-1][1], lows)

    def _age(self, held, mono_s, hold_s=None):
        if held is None:
            return None
        age = float(mono_s) - held[0]
        return age if age < (self.hold_s if hold_s is None else hold_s) \
            else None

    def update(self, readings, last_ok, now_s, mono_s):
        """(True / False / None, sentence): a shot of at least FIRING_MIN_J
        noticed less than `hold_s` ago. A trigger after it changes nothing."""
        on, why = firing(readings, last_ok, now_s)
        self._look(readings, last_ok, now_s, mono_s)
        if on is None:
            return on, why
        age = self._age(self._fire, mono_s)
        if age is not None:
            if on:
                return True, f"{why}\nLast shot {age:.0f} s ago."
            return True, (f"SBW4 {self._fire[1]:.3f} J {age:.0f} s ago — "
                          f"the laser is firing. The newer sample under "
                          f"{FIRING_MIN_J:g} J is a lone trigger, which does "
                          f"not stop it.\n{why}")
        if not on:
            return on, why
        if self._fire is None:
            return False, (f"{why}\nThat shot was already there when the "
                           "program started — waiting for the next one.")
        age = float(mono_s) - self._fire[0]
        return False, (f"{why}\nLast shot {age:.0f} s ago — idle after "
                       f"{self.hold_s:g} s without a new one.")

    def fresh_energy(self, mono_s):
        """(value in J, seconds ago) of what is being shot, else None. Call
        after `update` on the same pass.

        A high power shot noticed less than `hold_s` ago wins; else a low
        power stream (`LP_STREAM_N`) whose newest sample is that fresh. A lone
        low sample — a trigger — is never fresh energy. Nor is anything
        already there at start-up."""
        age = self._age(self._high, mono_s)
        if age is not None:
            return self._high[1], age
        age = self._age(self._stream, mono_s)
        if age is not None:
            return self._stream[1], age
        return None

    def high_age(self, mono_s, hold_s=HP_HOLD_S):
        """Seconds since the last high power shot when under `hold_s`, else
        None — what keeps an open shutter from meaning low power."""
        return self._age(self._high, mono_s, hold_s)


# ── the deformable mirror's set-up, judged by SBW4's energy ──────────────────
# Asked for on 8.10.2026: a "DM settings" line on the Overview list, there only
# while its Show is ticked on the Limits tab, saying which saved set-up the
# mirror's Applied voltages grid is. Low power cannot be on while we shoot at high power,
# and the other way round; low power shooting cannot be seen any other way, so
# SBW4's energy decides:
#
#   a fresh SBW4 shot over DM_SPLIT_J      the set-up must be ticked High power
#   a fresh STREAM of shots at or under    the set-up must be ticked Low power
#   it (a lone one is a trigger, 15.10.2026 — see LP_STREAM_N)
#
# Which set-ups count as which is ticked on the DM settings tab (9.10.2026:
# "když jich budu mít víc zaškrtnutých, tak pokud jedno z nich tam bude, tak to
# bude potom OK"), `ls_dm.tag_of`. A set-up ticked for neither — or a grid that
# is no saved set-up at all — is wrong for any fresh shot.
#
# Fresh is `FiringWatch`'s clock: a new sample, at most FIRING_HOLD_S ago. No
# fresh shot, nothing contradicts the mirror and the line is a tick. A cross
# makes the halls in force blink like any row out of range — even while the
# energy is under the firing threshold, because a High power fault happens
# exactly then.
#
# Low power shooting may well leave SBW4 silent rather than under 0.05 J, so
# it is also recognised the way the user put it on 9.10.2026: "pokud nebude
# SBW4 nad 0,05J a bude otevřený shutter a zároveň valve k experimentální hale,
# tak tam střílíme LP". The shutter is SAST1's state, the channel the user
# picked (`DM_SHUTTER_CHANNEL`, archived on change). 1 = OPEN, read off 6.10.2026:
# it goes to 1 as each burst of 20 J SBW4 shots starts and back to 0 as it ends
# (16:46:23 → 16:47:57, 16:52:28 → 16:54:02, …). The valve part is the
# caller's — the line is judged only while a hall's limits are in force, i.e.
# its valve is open.
DM_KIND = "dm"
DM_ROW_NAME = "DM settings"
DM_SPLIT_J = 0.05
DM_TAG_HIGH = "high"            # the same words as ls_dm.TAG_HIGH / TAG_LOW
DM_TAG_LOW = "low"
DM_SHUTTER_CHANNEL = "L3-SHUT_SAST1_Shutter_LT6_SP-MSS:State"
DM_SHUTTER_NAME = "SAST1"


def shutter_open(readings, last_ok, now_s):
    """True / False: is the SAST1 shutter open; None when unreadable."""
    reading = (readings or {}).get(DM_SHUTTER_CHANNEL)
    if not _readable(reading, (last_ok or {}).get(DM_SHUTTER_CHANNEL), now_s):
        return None
    try:
        return float(reading.value) >= 0.5
    except (TypeError, ValueError):
        return None


def dm_judge(tag, energy, name="the set-up", shutter=None, high_age=None):
    """(state, sentence) for the mirror's set-up against what is being shot.

    `tag` is DM_TAG_HIGH / DM_TAG_LOW for a set-up ticked as such, "" for a
    grid read fine but ticked for neither, None when the mirror is not known.
    `energy` is `FiringWatch.fresh_energy`: (J, seconds ago) or None — a lone
    trigger is never in it. `shutter` is `shutter_open`: True, False or None
    (not known). `high_age` is `FiringWatch.high_age`: seconds since the last
    high power shot while under HP_HOLD_S, else None.

        a fresh SBW4 shot over DM_SPLIT_J        high power is being shot
        else a fresh low power stream            low power is being shot
        else the shutter open, and no high
             power shot for HP_HOLD_S            low power is being shot
        else                                     nothing is being shot
    """
    if tag is None:
        return STATE_UNKNOWN, "the mirror's set-up is not known, so it is not " \
                              "judged"
    if energy is not None and energy[0] > DM_SPLIT_J:
        value, age = energy
        want, words = DM_TAG_HIGH, "High power"
        shot = f"SBW4 {value:.3f} J {age:.0f} s ago — over {DM_SPLIT_J:g} J"
    elif energy is not None:
        value, age = energy
        want, words = DM_TAG_LOW, "Low power"
        shot = (f"SBW4 shots at or under {DM_SPLIT_J:g} J keep coming, "
                f"{LP_STREAM_N} or more in {LP_STREAM_S:g} s (the last "
                f"{value:.3f} J, {age:.0f} s ago)")
    elif shutter and high_age is None:
        want, words = DM_TAG_LOW, "Low power"
        shot = (f"the {DM_SHUTTER_NAME} shutter is open and no SBW4 shot over "
                f"{DM_SPLIT_J:g} J came in the last {HP_HOLD_S:g} s")
    else:
        if tag not in (DM_TAG_HIGH, DM_TAG_LOW):
            return STATE_UNKNOWN, (f"{name} is ticked for neither High power "
                                   f"nor Low power — nothing is being shot")
        if shutter and high_age is not None:
            return STATE_OK, (f"{name} — the {DM_SHUTTER_NAME} shutter is "
                              f"open, but the last high power shot was "
                              f"{high_age:.0f} s ago: still a high power "
                              f"sequence, not low power, until "
                              f"{HP_HOLD_S:g} s without one")
        closed = (f"the {DM_SHUTTER_NAME} shutter is "
                  f"{'closed' if shutter is False else 'not readable'}")
        return STATE_OK, (f"{name} — no fresh SBW4 shot and {closed}, "
                          f"nothing to contradict it")
    if tag != want:
        return STATE_BAD, (f"{name} is not ticked for {words}, but {shot}: "
                           f"{words.lower()} is being shot")
    return STATE_OK, f"{name} is ticked for {words}, and {shot}"


# ── counting the shots ───────────────────────────────────────────────────────
# Asked for on 2.10.2026, with Image Slider's rules (`Image Tools/is_t.py`,
# `ShotCounter`): every time a number on the list CHANGES, that is a shot — one
# number changing is enough, they need not all change. When nothing has changed
# for `reset_s` seconds the number stays on screen but the count is zeroed, and
# the next shot shows 1 again.
#
# "Changes" is a new ARCHIVED SAMPLE, read off its timestamp, which is how the
# archiver writes these channels: on change only. Every sample is counted, not
# one per pass, because the reader already holds the last two minutes of
# timestamps (`ls_cpva.Reading.recent_ns`) — a laser firing faster than the
# one-second pass is still counted shot by shot.
#
# The channels are the SHOWN rows' ("jen zobrazené řádky"), see `shot_channels`.
# One shot lands on several of them "roughly together", one after another, with
# stamps that can be a second apart and in different passes. So, as in Image
# Slider's energy channels, every channel is counted on its own and the number
# is the LARGEST of those counts: three channels changing for one shot are one
# shot however far apart they changed (5.10.2026; a 0.2 s same-shot window
# before that counted a staggered shot two or three times).
SHOT_RESET_S_DEFAULT = 10           # Image Slider's own default
SHOT_RESET_LIMITS = (1, 3600)
LATE_TAIL_S = 2.0                   # how far one shot's channels can straggle
SHOT_KIND = "shots"
SHOT_ROW_NAME = "Shot counter"
# Not a row of the list on the Overview any more (5.10.2026): one line ABOVE
# the list, "Shot counter: 12", in smaller text than the list and with no
# verdict square. Its size is its own, not the list's, and the fit never
# changes it.
SHOT_SIZE_PX_DEFAULT = 16
SHOT_SIZE_LIMITS = (8, 200)
SHOT_BOLD_DEFAULT = False


class ShotCounter:
    """The number of shots in the current run. No Qt, so it is testable.

    Idle is timed on this PC's monotonic clock, never on the archiver's
    timestamps: this PC runs about 25 s ahead of the archiver.

    The first look at a channel is HISTORY and counts nothing — what was
    already archived when the program started (or when a row was ticked Show)
    is not a shot that just happened.
    """

    def __init__(self, reset_s=SHOT_RESET_S_DEFAULT):
        self.reset_s = float(reset_s)
        self.count = 0              # this run; zeroed when it goes idle
        self.shown = 0              # what the screen says; kept over a reset
        self._last_ns = {}          # channel -> newest stamp already seen
        self._per_channel = {}      # channel -> new samples this run
        self._last_shot_ns = 0      # newest stamp counted, for a run's late tail
        self._last_arrival = None   # monotonic time of the last shot

    def tick(self, mono_s):
        """End the run when it has been quiet for `reset_s`. `shown` stays."""
        if (self._last_arrival is not None
                and float(mono_s) - self._last_arrival >= self.reset_s):
            self.count = 0
            self._per_channel = {}
            self._last_arrival = None

    def running(self):
        return self._last_arrival is not None

    def update(self, readings, channels, mono_s):
        """Count the new samples of `channels` in `readings`. Returns `shown`."""
        self.tick(mono_s)
        added = False
        for ch in channels:
            reading = (readings or {}).get(ch)
            if reading is None or reading.error:
                continue
            stamps = [int(t) for t in (getattr(reading, "recent_ns", ()) or ())
                      if t]
            newest = getattr(reading, "newest_ns", None)
            if not stamps and newest:
                stamps = [int(newest)]
            if not stamps:
                continue
            top = max(stamps)
            last = self._last_ns.get(ch)
            if last is None:
                self._last_ns[ch] = top         # history, not a shot
                continue
            new = [t for t in stamps if t > last]
            if not new:
                continue
            self._last_ns[ch] = top
            if self._last_arrival is None:
                # No run going: a sample stamped up to `LATE_TAIL_S` after the
                # last counted shot is a late channel's share of the run that
                # already ended. Half the reset time at most, so a real new
                # shot is never taken for one.
                tail = int(min(LATE_TAIL_S, self.reset_s / 2) * 1e9)
                new = [t for t in new if t > self._last_shot_ns + tail]
                if not new:
                    continue
            self._per_channel[ch] = self._per_channel.get(ch, 0) + len(new)
            self._last_shot_ns = max(self._last_shot_ns, max(new))
            added = True
        if added:
            self.count = max(self._per_channel.values())
            self.shown = self.count
            self._last_arrival = float(mono_s)
        return self.shown

    def forget(self, channel):
        """A channel left the list: its next look is history again."""
        self._last_ns.pop(channel, None)
        self._per_channel.pop(channel, None)

    def sentence(self):
        """What the counter's tooltip says about how it counts."""
        state = ("counting" if self.running()
                 else "idle — the next shot starts again from 1")
        return (f"A shot is the shown rows changing; rows changing one "
                f"after another for the same shot count once.\nAfter "
                f"{self.reset_s:g} s without one the number stays here and the "
                f"count starts again from 1 with the next shot.\nNow: {state}.")


# ─────────────────────────────────────────────────────────────────────────────
# Timing
# ─────────────────────────────────────────────────────────────────────────────
POLL_MS = 1000               # one pass a second; every channel here is a setting
PASS_DEADLINE_S = 2.5        # a whole pass, however many channels it has
POLL_WORKERS = 6

# How long a channel may go unread before its row stops claiming anything. This
# is the age of the last successful READ, never the age of the value: the
# archiver writes on change only, so a setting nobody has touched since Monday
# is perfectly healthy and its value is hours old.
STALE_AFTER_S = 30.0

# The beat of a hall that has something wrong with it. 450 ms is the sibling
# program's trip blink, and it is driven by a PRECISE timer — a default QTimer
# asked for 33 ms fires at 21 Hz on Windows, and the same coarseness makes a
# blink visibly uneven.
BLINK_MS = 450

# ─────────────────────────────────────────────────────────────────────────────
# Verdicts
# ─────────────────────────────────────────────────────────────────────────────
STATE_OK = "ok"              # green square, white tick
STATE_BAD = "bad"            # red square, black cross
STATE_UNKNOWN = "unknown"    # grey square, one dash — nothing is claimed


def parse_level(text):
    """A limit box: the number in it, or None when it is empty (limit off).

    Empty is not zero. A comma is accepted as the decimal point, because that is
    what a Czech keyboard produces.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
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


# ── units, and how many digits a reading is shown to ────────────────────────
# `fs^2` is how a unit is TYPED and `fs²` is how it belongs on screen. Asked for
# on 1.10.2026: "a ^2 dej do horního indexu".
#
# Done at DISPLAY time and nowhere else. The Unit box on the Limits tab goes on
# showing exactly what was typed into it, because a box that rewrites what you
# typed the moment you leave it is a box nobody trusts — and because the
# settings file should hold what he wrote, not a rendering of it. A unit that
# already carries a real superscript is left alone, so either way of typing it
# works.
_SUPERSCRIPTS = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
                 "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
                 "-": "⁻", "+": "⁺"}
_CARET_RE = re.compile(r"\^([-+]?\d+)")


def unit_text(unit):
    """A unit as it belongs on screen: `fs^2` becomes `fs²`.

    Only `^` followed by a whole number is lifted. A lone caret, or `^x`, is
    left exactly as it was — there is no superscript for it and inventing one
    would be worse than printing the caret.
    """
    unit = (unit or "").strip()
    if "^" not in unit:
        return unit
    return _CARET_RE.sub(
        lambda m: "".join(_SUPERSCRIPTS.get(ch, ch) for ch in m.group(1)),
        unit)


# How many decimal places a reading is shown to, by its unit, lower-cased.
# Energies to hundredths — "energii zarovnej na setiny", 1.10.2026 — because
# 19.9294 J is three digits of shot-to-shot noise after the one that matters,
# and a number whose last digits dance is a number that is read more slowly.
# A unit that is not in here keeps the shortest form that loses nothing.
UNIT_DECIMALS = {"j": 2, "mj": 2}


def value_text(value, unit=""):
    """A reading as it belongs on screen. Never "None"."""
    if value is None:
        return "—"
    places = UNIT_DECIMALS.get((unit or "").strip().lower())
    if places is not None:
        text = f"{value:.{places}f}"
    else:
        text = (f"{value:g}" if abs(value) >= 0.001 or value == 0
                else f"{value:.3g}")
    return f"{text} {unit_text(unit)}".strip()


def range_text(lo, hi):
    """The limits of one hall in words, for a column and for a tooltip."""
    if lo is None and hi is None:
        return "no limits"
    if lo is None:
        return f"up to {level_text(hi)}"
    if hi is None:
        return f"from {level_text(lo)}"
    return f"{level_text(lo)} … {level_text(hi)}"


def judge(value, lo, hi):
    """(state, sentence) for one reading against one hall's limits.

    Inclusive on both sides: `min <= value <= max`. A limit left empty is not a
    limit, and both empty means there is nothing to judge — which is not the
    same as passing, so it comes back unknown.
    """
    if value is None:
        return STATE_UNKNOWN, "nothing read"
    if lo is None and hi is None:
        return STATE_UNKNOWN, "no limits set"
    if lo is not None and value < lo:
        return STATE_BAD, f"below {level_text(lo)}"
    if hi is not None and value > hi:
        return STATE_BAD, f"above {level_text(hi)}"
    return STATE_OK, f"in {range_text(lo, hi)}"


# ─────────────────────────────────────────────────────────────────────────────
# The watched channels
# ─────────────────────────────────────────────────────────────────────────────
# One row is:
#
#   {"id": 3,
#    "pv": "L3-...:Delay",          the real channel name
#    "name": "Front end delay",     what the first tab calls it
#    "unit": "ns",
#    "limits": {"E2": {"min": 10.0, "max": 20.0, "show": true}, ...}}
#
# The id is saved and never reused: it is what lets a reading, a table row and a
# square keep pointing at the same channel across an edit.
#
# A row can also be a FORMULA, and then it carries two more keys and no channel
# name of its own:
#
#   {"id": 7, "pv": "", "name": "SBW4 after the compressor", "unit": "J",
#    "expr": "A * 0.749", "bind": {"A": 3}, "limits": {...}}
#
# `expr` is the expression as it was typed, in the letters the table shows;
# `bind` says which ROW each letter meant when it was typed. Ids, not names: a
# formula must not come unstuck because somebody renamed a channel or pointed a
# row at a different one, and the id is the thing this program never reuses.
# The letters themselves are only a drawing — they are the rows' positions, so
# they shift as the list is edited and the expression is rewritten from `bind`.


def new_row(row_id, pv="", name="", unit=""):
    """A fresh watched channel: shown, and limited in no hall.

    A channel put on the list in one hall is on the list in all five, and only
    Min and Max belong to the hall where they were typed. `show` sits on the
    row, not in `limits` — it is one switch for the whole program.
    """
    return {"id": int(row_id), "pv": str(pv), "name": str(name),
            "unit": str(unit), "show": True, "alarm": True,
            "scan_min": None, "scan_max": None,
            "limits": {h: {"min": None, "max": None} for h in HALLS}}


def new_formula_row(row_id, name="", expr="", unit="", bind=None):
    """A fresh formula row: no channel of its own, a number it works out."""
    row = new_row(row_id, "", name, unit)
    row["expr"] = str(expr)
    row["bind"] = {str(k): int(v) for k, v in (bind or {}).items()}
    return row


def new_shot_row(row_id, name=SHOT_ROW_NAME):
    """The built-in shot counter's row: no channel, the number is counted.

    At most one on the list. It is shown, named and limited like any other row;
    what it counts is `ShotCounter`'s, from the channels of the shown rows.
    """
    row = new_row(row_id, "", name, "")
    row["kind"] = SHOT_KIND
    return row


def is_shot_row(row):
    """True for the built-in shot counter's row."""
    return row.get("kind") == SHOT_KIND


def new_dm_row(row_id, name=DM_ROW_NAME):
    """The built-in DM settings row: no channel, no limits — its verdict is
    `dm_judge`'s. Show UNTICKED: nothing changes until somebody ticks it."""
    row = new_row(row_id, "", name, "")
    row["kind"] = DM_KIND
    row["show"] = False
    return row


def is_dm_row(row):
    """True for the built-in DM settings row."""
    return row.get("kind") == DM_KIND


def is_builtin_row(row):
    """The shot counter or DM settings: rows that read no channel."""
    return is_shot_row(row) or is_dm_row(row)


def shot_channels(rows):
    """The channels whose new samples count as shots: the SHOWN rows'.

    A shown plain row gives its channel; a shown formula gives the channels of
    the rows it is worked out from, followed down through formulas of formulas.
    The counter itself and an unticked row give nothing. Once each, in order.
    """
    by_id = {int(r.get("id", 0)): r for r in rows}
    out = []

    def take(row, depth=0):
        if depth > len(rows) or is_shot_row(row):
            return
        if is_formula_row(row):
            for rid in (row.get("bind") or {}).values():
                src = by_id.get(int(rid))
                if src is not None:
                    take(src, depth + 1)
            return
        pv = (row.get("pv") or "").strip()
        if pv and pv not in out:
            out.append(pv)

    for row in rows:
        if is_shown(row):
            take(row)
    return out


def is_formula(row):
    """True when this row works its number out instead of reading it."""
    return bool((row.get("expr") or "").strip())


def is_formula_row(row):
    """True when this row IS a formula, even before anything has been typed.

    The key being there at all is the marker, empty or not, and this is the
    question the Settings tab must ask — not `is_formula`.

    Measured on 30.9.2026: a freshly added formula row has `expr` empty, so
    `is_formula` said False, so the first thing typed into the row's own box was
    taken as a CHANNEL NAME. One operator ended up with a row whose channel was
    literally `C*0.749`, asked of the archiver once a second, and a "Reading
    now" column that was empty for a fortnight with nothing on screen to say
    why. `normalise` keeps the empty key for the same reason and repairs that
    shape when it finds it.
    """
    return "expr" in row


def next_id(rows):
    """A fresh id. Ids are SAVED and never reused."""
    return max([int(r.get("id", 0)) for r in rows] or [0]) + 1


def limits_for(row, hall):
    """One hall's limits of a row, made if they are not there yet.

    Written into the row rather than returned as a copy: a hall added to `HALLS`
    later, or a settings file saved by an older version, must not make the table
    fail — it fills itself in on first use.

    `show` is STRIPPED here, the way `gate_for` strips `closed` and `shoot_when`.
    It used to live per hall and it does not any more, and a dead flag left in
    the file is the first thing the next person reaching for an explanation
    would believe.
    """
    lim = row.setdefault("limits", {})
    entry = lim.get(hall)
    if not isinstance(entry, dict):
        entry = {"min": None, "max": None}
        lim[hall] = entry
    entry.setdefault("min", None)
    entry.setdefault("max", None)
    entry.pop("show", None)
    return entry


def row_label(row):
    """What to call this channel on screen: its shown name, else the channel."""
    name = (row.get("name") or "").strip()
    if name:
        return name
    pv = (row.get("pv") or "").strip()
    return pv or "(no channel)"


def is_shown(row):
    """True when this row is watched at all — the one Show tick box.

    It decides whether the row appears on the first tab, and whether it can
    fault a hall. ONE switch for the whole program: unticked in E2 is unticked
    in Int and E5 too, and the row leaves the first tab there and then.

    So the list on the first tab does not change as the laser moves from hall to
    hall — a row not shown is not shown anywhere, and the operator never loses
    their place in the list because the machine switched halls. What IS per hall
    is Min and Max, and nothing else.
    """
    return bool(row.get("show", True))


def is_alarmed(row):
    """True when an out-of-range reading on this row is REPORTED — the Alarm
    tick box on the Limits tab.

    Unticked, the row stays on the first tab with its number, but it is not
    judged: its square is a grey dash and it can make no hall blink. Asked for
    on 6.10.2026 for a GDD scan, which leaves the limits on purpose
    ("vyjedeme záměrně z mezí, tak aby to neblikalo"). One switch for every
    hall, like Show.
    """
    return bool(row.get("alarm", True))


# ── scanning ─────────────────────────────────────────────────────────────────
# Asked for on 8.10.2026: a GDD / TOD scan leaves the normal limits on purpose,
# and unticking Alarm stops checking altogether. Every row may carry a pair of
# SCAN limits of its own (Scan min / Scan max on the Limits tab), one pair for
# every hall, so it sits on the row like Show and Alarm and not in `limits`.
#
# Since 12.10.2026 the switch is ONE tick box on the Overview, for the whole
# list (`cfg["scanning"]`): "pokud se tenhle checkbox zapne, tak se pro každé PV
# přepnou limity, které tam budou nastavené pro scanning mode". On, every row
# with a scan limit filled in is judged against its scan pair; a row with both
# scan boxes empty keeps its hall's Min and Max (the user's choice). Off, back
# to Min and Max at once. While it is on, a square that is shooting says
# "SHOOTING + SCANNING".
#
# Alarm still wins: a row with Alarm unticked is not judged, scanning or not.
# The per-row Scanning tick box and the GDD / TOD / FOD name rule it came with
# are gone; `normalise` turns an old tick into the global switch.


def can_scan(row):
    """True for a row that may carry scan limits: every row but the built-in
    ones (the shot counter and DM settings have no limits at all)."""
    return not is_builtin_row(row)


def has_scan_limits(row):
    """True when Scan min or Scan max is filled in on a row that may scan."""
    return can_scan(row) and (row.get("scan_min") is not None
                              or row.get("scan_max") is not None)


def is_scanning(row, scanning):
    """True when `row` is judged against its scan pair right now: the
    Overview's Scanning box is ticked and the row has a scan limit."""
    return bool(scanning) and has_scan_limits(row)


def limits_in_force(row, hall, scanning=False):
    """(lo, hi) the row is judged against in `hall` right now.

    The scan pair while scanning is on and the row has one — the same in
    every hall — and the hall's own Min and Max otherwise.
    """
    if is_scanning(row, scanning):
        return row.get("scan_min"), row.get("scan_max")
    entry = limits_for(row, hall)
    return entry.get("min"), entry.get("max")


def scanning_rows(rows, scanning):
    """The shown rows judged on scan limits right now."""
    return [r for r in rows or []
            if is_shown(r) and is_scanning(r, scanning)]


# ─────────────────────────────────────────────────────────────────────────────
# Formulas
# ─────────────────────────────────────────────────────────────────────────────
# The same shape Image Tools uses (`is_t.py`, `PV_DERIVED` / `pv_eval_derived`):
# every row wears a LETTER, and a formula is an expression over those letters.
# The repo keeps no shared library, so the two are copies — keep them in step by
# hand if either changes.
#
# The expression is compiled with NO builtins: a settings file is a file like
# any other, and one that has been edited by hand must not be able to run
# anything. Four names are handed in, and that is all there is.
_EXPR_VAR_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z]+(?![A-Za-z0-9_])")
# A comma between two digits, which on a Czech keyboard is a decimal point.
_DECIMAL_COMMA_RE = re.compile(r"(?<=\d)\s*,\s*(?=\d)")
_EXPR_ENV = {"__builtins__": {}, "abs": abs, "min": min, "max": max,
             "round": round, "math": math}
# Compiled once and kept, keyed by the text. An expression that will not
# compile is remembered as False so a broken formula is not re-parsed once a
# second for the rest of the day.
_EXPR_CACHE = {}


def row_letter(i):
    """The letter of the row at position `i`: A … Z, AA, AB …"""
    letters = ""
    i = int(i)
    while True:
        letters = chr(ord("A") + i % 26) + letters
        i = i // 26 - 1
        if i < 0:
            return letters


def letters_for(rows):
    """{row id: its letter} for the list as it stands right now."""
    return {int(r.get("id", 0)): row_letter(i) for i, r in enumerate(rows)}


def expr_vars(expr):
    """The letters an expression names, in the order they first appear."""
    out = []
    for name in _EXPR_VAR_RE.findall(expr or ""):
        if name not in out:
            out.append(name)
    return out


def _split_on_brackets(expr):
    """The expression cut into pieces, each with the bracket depth it sits at.

    [(depth, text), …] in order, so a rule can be applied to the part OUTSIDE
    brackets without touching what is inside them. Unbalanced brackets are not
    an error here — the depth simply never comes back to zero, and
    `formula_problems` is what reports the expression as unreadable.
    """
    out = []
    depth = 0
    start = 0
    for i, ch in enumerate(expr):
        if ch in "([{":
            out.append((depth, expr[start:i + 1]))
            depth += 1
            start = i + 1
        elif ch in ")]}":
            out.append((depth, expr[start:i]))
            depth = max(0, depth - 1)
            start = i
    out.append((depth, expr[start:]))
    return out


def fix_decimal_commas(expr):
    """`A * 0,749` → `A * 0.749`, without touching `min(A, B)`.

    A Czech keyboard produces a comma, and the Min and Max boxes have always
    accepted one (`parse_level`). A formula did not, and it failed in the worst
    possible way: `A * 0,749` is VALID Python — it is the tuple `(A*0, 749)` —
    so it compiled, nothing went red, and it came out once a second as the
    useless "the formula did not work out to a number".

    Only commas OUTSIDE brackets are swapped, because that is the only place a
    comma can possibly be a decimal point. Inside them it separates arguments:
    a blanket `replace(",", ".")` would wreck `min(A, B)`, and a plain
    digit-comma-digit regex would quietly turn `min(1, 2)` into `min(1.2)`.

    A decimal comma inside brackets is therefore left exactly as typed — it
    cannot be told apart from an argument separator, and `formula_problems`
    says why it does not try. The tooltip on the box is where the operator is
    told: outside brackets a comma is a decimal point, inside them it is not.
    """
    return "".join(_DECIMAL_COMMA_RE.sub(".", part) if depth == 0 else part
                   for depth, part in _split_on_brackets(expr or ""))


def expr_to_display(row, rows):
    """The stored expression written in the letters the table shows today.

    The letters move as rows are added, removed or reordered; `bind` is what
    the formula actually means, so the box is always drawn from it.
    """
    expr = (row.get("expr") or "").strip()
    bind = row.get("bind") or {}
    now = letters_for(rows)
    swap = {letter: now.get(int(rid)) for letter, rid in bind.items()
            if now.get(int(rid))}
    if not swap:
        return expr
    return _EXPR_VAR_RE.sub(lambda m: swap.get(m.group(0), m.group(0)), expr)


def expr_from_display(text, rows):
    """What was typed → (expression, bindings), captured against today's list.

    Every letter typed is resolved to the ROW at that position, there and then.
    A letter past the end of the list is left unbound, which is what the red
    box and the tooltip are for.

    A decimal comma is turned into a dot on the way IN, so the file never holds
    an expression that only looks as if it works — see `fix_decimal_commas`.
    """
    expr = (text or "").strip()
    if expr.startswith("="):
        expr = expr[1:].strip()
    expr = fix_decimal_commas(expr)
    by_letter = {row_letter(i): int(r.get("id", 0))
                 for i, r in enumerate(rows)}
    bind = {v: by_letter[v] for v in expr_vars(expr) if v in by_letter}
    return expr, bind


def formula_problems(row, rows):
    """Everything wrong with one formula row, in plain sentences.

    Empty means it is fine. This is what turns the box red and what its tooltip
    spells out, so it names every letter — a formula nobody can read is worse
    than no formula.
    """
    expr = (row.get("expr") or "").strip()
    if not expr:
        return ["This formula is empty — type something like A * 0.749."]
    out = []
    bind = row.get("bind") or {}
    by_id = {int(r.get("id", 0)): i for i, r in enumerate(rows)}
    now = letters_for(rows)
    for letter in expr_vars(expr):
        rid = bind.get(letter)
        shown = now.get(int(rid)) if rid is not None else letter
        if rid is None or int(rid) not in by_id:
            out.append(f"{letter} is not one of the rows on the list.")
        elif int(rid) == int(row.get("id", 0)):
            out.append(f"{shown} is this row itself.")
    # A source may sit anywhere on the list since the rows can be dragged into
    # any order (12.10.2026) — `row_values` works the formulas out in the order
    # they need. What cannot be worked out is a ring of formulas that wait
    # for each other.
    if not out and _in_formula_loop(row, rows):
        out.append("This formula and the formulas it uses wait for each other "
                   "in a loop — one of them has to use a channel instead.")
    # A decimal comma INSIDE brackets is deliberately NOT reported. `min(1, 2)`
    # and `min(A, 0,749)` are the same shape — a comma between two digits — and
    # the language underneath accepts both, so there is nothing here that can
    # tell a decimal point from an argument separator. Turning a correct
    # `min(1, 2)` red would teach the operator to ignore red boxes, which costs
    # more than the rare case it would catch. It is said in the tooltip and in
    # the ReadMe instead: outside brackets a comma is a decimal point, inside
    # them it is not.
    try:
        compile(expr, "<formula>", "eval")
    except Exception as exc:
        out.append(f"The expression could not be read — {exc}")
    return out


def _formula_sources(row):
    """The row ids a formula row reads, in the order its letters appear."""
    bind = row.get("bind") or {}
    out = []
    for letter in expr_vars((row.get("expr") or "").strip()):
        rid = bind.get(letter)
        if rid is not None and int(rid) not in out:
            out.append(int(rid))
    return out


def _in_formula_loop(row, rows):
    """True when following `row`'s sources through other formulas leads back
    to `row` itself."""
    by_id = {int(r.get("id", 0)): r for r in rows}
    start = int(row.get("id", 0))
    todo = list(_formula_sources(row))
    seen = set()
    while todo:
        rid = todo.pop()
        if rid == start:
            return True
        if rid in seen:
            continue
        seen.add(rid)
        src = by_id.get(rid)
        if src is not None and is_formula(src):
            todo.extend(_formula_sources(src))
    return False


class RowValue(NamedTuple):
    """One row's number as everything on screen sees it.

    `value` is the number or None; `problem` is the sentence to show instead of
    a number; `seen` is when the reading behind it last arrived, which is what
    makes a formula exactly as stale as its oldest source.
    """
    value: float | None
    problem: str | None
    seen: float | None


def _read_value(row, readings, last_ok, now_s):
    """A plain row's number, straight off the archiver."""
    pv = (row.get("pv") or "").strip()
    if not pv:
        return RowValue(None, "no channel set", None)
    reading = (readings or {}).get(pv)
    seen = (last_ok or {}).get(pv)
    if reading is None:
        return RowValue(None, "not read yet", seen)
    if reading.error:
        return RowValue(None, reading.error, seen)
    if reading.value is None:
        return RowValue(None, "nothing archived for this channel", seen)
    if seen is None or now_s - seen > STALE_AFTER_S:
        age = "" if seen is None else f" for {int(now_s - seen)} s"
        return RowValue(None, f"not read{age}", seen)
    return RowValue(float(reading.value), None, seen)


def _work_out(row, rows, done, now_s):
    """A formula row's number, from the rows already worked out.

    A source that has no number makes the formula have no number: a computed
    value is never a tick while something under it is a dash.
    """
    trouble = formula_problems(row, rows)
    if trouble:
        return RowValue(None, trouble[0], None)
    expr = (row.get("expr") or "").strip()
    bind = row.get("bind") or {}
    space = {}
    seen = None
    for letter in expr_vars(expr):
        src = done.get(int(bind[letter]))
        if src is None or src.value is None:
            why = "not read yet" if src is None else src.problem
            shown = letters_for(rows).get(int(bind[letter]), letter)
            return RowValue(None, f"{shown}: {why}", None)
        space[letter] = src.value
        if src.seen is not None:
            seen = src.seen if seen is None else min(seen, src.seen)
    code = _EXPR_CACHE.get(expr)
    if code is None:
        try:
            code = compile(expr, "<formula>", "eval")
        except Exception:
            code = False
        _EXPR_CACHE[expr] = code
    if code is False:
        return RowValue(None, "the expression could not be read", seen)
    try:
        result = eval(code, _EXPR_ENV, space)     # noqa: S307 — sandboxed above
    except Exception as exc:
        return RowValue(None, f"the formula did not work out — {exc}", seen)
    if isinstance(result, bool) or not isinstance(result, (int, float)) \
            or not math.isfinite(float(result)):
        return RowValue(None, "the formula did not work out to a number", seen)
    return RowValue(float(result), None, seen)


def row_values(rows, readings, last_ok, now_s=None, shots=None):
    """{row id: RowValue} — the one place a row turns into a number.

    Every row that is not a formula first, then the formulas in the order
    they need each other — a formula may use any row on the list, above or
    below it, since the list can be dragged into any order (12.10.2026).
    Everything on screen and every verdict comes through here, which is
    what keeps "a missing reading is never a tick" in one place.

    `shots` is the shot counter's number (`ShotCounter.shown`). It is counted
    here, not read, so it is never stale; None before there is a counter.
    """
    now = time.time() if now_s is None else now_s
    out = {}
    formulas = []
    for row in rows:
        rid = int(row.get("id", 0))
        if is_shot_row(row):
            out[rid] = (RowValue(float(shots), None, now) if shots is not None
                        else RowValue(None, "not counted yet", None))
        elif is_dm_row(row):
            # A set-up name, not a number: the Overview writes it itself.
            out[rid] = RowValue(None, "the mirror's set-up, not a number",
                                None)
        elif is_formula(row):
            formulas.append(row)
        else:
            out[rid] = _read_value(row, readings, last_ok, now)
    # A formula is worked out once every formula it reads has been; what is
    # still waiting when nothing more can be done is a loop or a broken
    # binding, and `_work_out` says which.
    formula_ids = {int(r.get("id", 0)) for r in formulas}
    waiting = formulas
    while waiting:
        later = []
        for row in waiting:
            if any(rid in formula_ids and rid not in out
                   for rid in _formula_sources(row)):
                later.append(row)
            else:
                out[int(row.get("id", 0))] = _work_out(row, rows, out, now)
        if len(later) == len(waiting):
            for row in later:
                out[int(row.get("id", 0))] = _work_out(row, rows, out, now)
            break
        waiting = later
    # In list order, as the callers have always had it.
    return {int(r.get("id", 0)): out[int(r.get("id", 0))] for r in rows
            if int(r.get("id", 0)) in out}


def judge_row(row, value, halls_active, scanning=False):
    """The verdict for one row as the first tab shows it: (state, sentence).

    `value` is the row's `RowValue` from `row_values`. `halls_active` is the
    halls whose limits are in force — `halls_in_force`, not the raw valve
    states, so Int's limits count while the experimental valves are silent. With
    none of them the row is not judged at all, see the module docstring. With
    more than one the worst verdict wins and the sentence names the hall it came
    from, because that is the one the operator has to go and look at.

    An unticked row is never judged, in any hall: Show is one switch.
    """
    if value is None:
        return STATE_UNKNOWN, "not read yet"
    if value.value is None:
        return STATE_UNKNOWN, value.problem or "not read yet"
    if not halls_active:
        return STATE_UNKNOWN, "no hall is being shot into"
    if not is_shown(row):
        return STATE_UNKNOWN, "not watched"

    state, text = _worst_verdict(row, value.value, halls_active, scanning)
    if not is_alarmed(row):
        # Judged for the sentence only: the tooltip still says where the
        # number stands, the square says nothing.
        return STATE_UNKNOWN, f"alarm off — {text or 'not judged'}"
    return state, text


# ── the little square beside a reading ───────────────────────────────────────
# Asked for on 12.10.2026: grey means ONE thing — the number cannot be read
# ("něco typu network error"). A number that is read fine while nothing is
# being shot is ORANGE, with the tick or cross it would get, so the operator
# still sees where it stands but knows it is not being checked ("to teda není
# potřeba kontrolovat"). A number with nothing to judge it by (no limits, or
# Alarm unticked) is a white square with a dash. Only green and red come from a
# real shot, and only red makes a hall blink — `hall_faults` is unchanged.
MARK_IDLE_OK = "idle_ok"        # orange square, white tick
MARK_IDLE_BAD = "idle_bad"      # orange square, black cross
MARK_NONE = "none"              # white square, grey dash — read, not judged


def row_mark(row, value, halls_active, firing, idle_hall, scanning=False):
    """(square, sentence) for one row of the Overview's list.

    `firing` is whether SBW4 says a shot is going on (`FiringWatch`);
    `idle_hall` is the hall whose limits the tick or cross is worked out
    against while nothing is being shot — `single_hall`, Int when the beam
    goes nowhere else.
    """
    if value is None or value.value is None:
        return STATE_UNKNOWN, (value.problem if value is not None else None) \
            or "not read yet"
    if not is_shown(row):
        return STATE_UNKNOWN, "not watched"
    shooting = bool(firing) and bool(halls_active)
    halls = list(halls_active) if shooting else [idle_hall or FALLBACK_HALL]
    state, text = _worst_verdict(row, value.value, halls, scanning)
    if not shooting:
        text = f"not shooting — {text}" if text else "not shooting"
    if not is_alarmed(row):
        return MARK_NONE, f"alarm off — {text or 'not judged'}"
    if state == STATE_UNKNOWN:
        return MARK_NONE, text
    if shooting:
        return state, text
    return (MARK_IDLE_BAD if state == STATE_BAD else MARK_IDLE_OK), text


def _worst_verdict(row, value, halls_active, scanning=False):
    worst, worst_text = STATE_OK, ""
    scan = is_scanning(row, scanning)
    for hall in halls_active:
        state, text = judge(value, *limits_in_force(row, hall, scanning))
        if scan:
            text = f"scan: {text}"
        wording = (f"{hall_shown(hall)}: {text}" if len(halls_active) > 1
                   else text)
        if state == STATE_BAD:
            return STATE_BAD, wording
        if state == STATE_UNKNOWN and worst == STATE_OK:
            worst, worst_text = STATE_UNKNOWN, wording
        elif state == STATE_OK and not worst_text:
            worst_text = wording
    return worst, worst_text


def hall_faults(rows, values, hall, scanning=False):
    """The rows that are out of range for one hall. Only the shown ones.

    A row whose Show is unticked is not watched at all, so it cannot make any
    hall blink. Neither can one whose Alarm is unticked. `values` is
    `row_values`, so a row with no number, a stale one and a formula whose
    source is missing are all skipped rather than blamed.
    """
    out = []
    for row in rows:
        if not is_shown(row) or not is_alarmed(row) or is_builtin_row(row):
            continue                    # the built-in rows have no limits
        value = (values or {}).get(int(row.get("id", 0)))
        if value is None or value.value is None:
            continue
        state, _text = judge(value.value,
                             *limits_in_force(row, hall, scanning))
        if state == STATE_BAD:
            out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# How the program is doing, in one sentence
# ─────────────────────────────────────────────────────────────────────────────
# These two used to be built inside the Overview tab and written along its
# bottom edge. They live here because the Info / Log tab says them now, and a
# sentence with a copy in two tabs is a sentence that ends up saying two
# different things.

def status_line(rows, shooting, readings, last_ok, now_s=None, screen=None,
                scanning=False):
    """Whose limits are in force, how many channels, and how they are doing."""
    now = time.time() if now_s is None else now_s
    rows = list(rows or [])
    watched = sum(1 for r in rows if is_shown(r))
    how_many = f"{watched} channel{'' if watched == 1 else 's'}"

    active, fallback = halls_in_force(shooting, readings, last_ok, now, screen)
    if not active:
        return f"No hall is being shot into — {how_many} listed, nothing judged"

    values = row_values(rows, readings, last_ok, now)
    bad = sum(len(hall_faults(rows, values, h, scanning)) for h in active)
    verdict = f", {bad} out of range" if bad else ", all in range"
    where = ", ".join(hall_shown(h) for h in active)
    if not fallback:
        return f"Shooting into {where} — {how_many} watched{verdict}"
    # Why Int's limits are the ones in force. It is the normal state of the
    # machine today, not something gone wrong, so it is a sentence and not a
    # warning.
    others = ", ".join(h for h in HALLS if h not in active)
    return (f"The {others} valves cannot be read, so {where}'s limits are in "
            f"force — {how_many} watched{verdict}")


def read_clock_text(last_ok, now_s=None):
    """When the last reading of anything arrived, in words."""
    now = time.time() if now_s is None else now_s
    newest = max(last_ok.values()) if last_ok else None
    if newest is None:
        return "nothing read yet"
    age = now - newest
    return "read just now" if age < 2 else f"last read {int(age)} s ago"


# ─────────────────────────────────────────────────────────────────────────────
# Finding a channel by name
# ─────────────────────────────────────────────────────────────────────────────

def search_channels(names, query, limit=400):
    """Find channel names the way an operator types: (matches, total).

    The rule, which is the same one the other programs here follow:

    * the query is split into WORDS, and every word has to appear somewhere in
      the name. Nothing has to be at the start, and nothing has to be adjacent
      — "chl temp" finds `L3-UTIL-CHL03-001:Temp`;
    * the ORDER of the words does not matter: "temp chl" gives exactly what
      "chl temp" gives, in the same ranking;
    * commas, semicolons and `*` separate words just like spaces;
    * a fragment that fits several channels returns THOSE channels. It is a
      filter, never an error and never the whole list back.

    `total` is how many matched before `limit` cut the list, so the caller can
    say "showing 400 of 1620" rather than silently showing part of an answer.
    """
    words = [w for w in re.split(r"[\s,;*]+", (query or "").lower()) if w]
    if not words:
        return [], 0
    scored = []
    for name in names:
        s = _rank_channel(name, words)
        if s is not None:
            scored.append(((s, len(name), name), name))
    scored.sort()
    return [n for _s, n in scored[:limit]], len(scored)


def _rank_channel(name, words):
    """Sort weight of one channel (lower = better), or None when a word is
    missing. Each word is graded on its own — exact name 0, exact field (the
    part after the last ':') 1, a prefix 2, inside the field 3, anywhere 4 —
    the weakest word sets the tier and the sum breaks ties. Nothing here looks
    at the order the words were typed in."""
    low = name.lower()
    field = low.rsplit(":", 1)[-1]
    worst = total = 0
    for w in words:
        if w == low:
            s = 0
        elif w == field:
            s = 1
        elif low.startswith(w) or field.startswith(w):
            s = 2
        elif w in field:
            s = 3
        elif w in low:
            s = 4
        else:
            return None
        worst = max(worst, s)
        total += s
    return worst * 10 + min(total, 9)


# ─────────────────────────────────────────────────────────────────────────────
# Failures, in plain language
# ─────────────────────────────────────────────────────────────────────────────
# A raw traceback tells an operator nothing. Every failure that can reach the
# screen is turned into a sentence plus a hint that says what to try. Carried
# over from `Announcer/ann_core.py`, which is where these words were written.

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
          "The archiver doesn't know this channel name. It may be misspelled or simply "
          "not archived."),
    500: ("Archiver server error",
          "The archiver was reached and answered, but crashed internally while handling the "
          "request. A server-side problem — usually temporary, not your network."),
    502: ("Archiver gateway error",
          "A gateway in front of the archiver got a broken reply from it."),
    503: ("Archiver unavailable",
          "The archiver is temporarily down, overloaded, or restarting. Usually clears up "
          "on its own."),
    504: ("Archiver gateway timeout",
          "A gateway forwarded the request but the archiver behind it never replied in time."),
}

_NETWORK_HINTS = {
    "Connection timed out":
        "The request left your PC but no reply came back within the time limit. The archiver "
        "is reachable in principle but too slow, overloaded, or the network is congested.",
    "Archiver unreachable":
        "We couldn't open a connection to the archiver at all — server is down, the address "
        "is wrong, or you're not on the right network. Nothing was sent.",
    "Connection failed":
        "The connection to the archiver was actively refused or dropped mid-way. The server "
        "(or a firewall) said 'no' rather than just staying silent.",
    "Invalid server response":
        "The archiver answered, but the data wasn't readable (not valid JSON). The server may "
        "be returning an error page instead of data.",
    "Archiver too busy":
        "Every connection to the archiver was already in use, so this read never left the PC. "
        "Nothing is wrong with the channel — the next pass will read it.",
}


def _from_archiver_message(text):
    """Translate `ls_cpva`'s own failure text: (detail, hint) or None."""
    t = (text or "").strip()
    parts = t.split()
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
        hint = "An unexpected error occurred while reading this channel."
    return f"{pv_name} — {detail}", hint


# ─────────────────────────────────────────────────────────────────────────────
# The settings file
# ─────────────────────────────────────────────────────────────────────────────
# Under APPDATA, not beside the program. The channel list and the limits are the
# operator's own work and must survive a redeploy — the program folder is
# replaced wholesale when a new version is copied out, and a built copy's folder
# can be read-only.

APP_DIR_NAME = "LaserSettings"
CONFIG_NAME = "laser_settings.json"
CONFIG_VERSION = 1

# How the first tab's list is drawn. A setting, not a constant in the drawing
# code: the list is read from across the room and how big "readable" is depends
# on the room and on the screen, so the Settings tab owns these four numbers.
LOOK_DEFAULTS = {"family": "Segoe UI", "size_px": 19, "bold": True,
                 "mark_px": 30}

# The bounds the Settings tab offers and the file is repaired against. They are
# wide on purpose, and both ends were asked for on 1.10.2026:
#
#   the ceiling — "zvyš maximum fontu třeba až na 500 nebo něco.. prostě do
#   doby, kde to nebude dělat problémy, ale dá se to nastavit i na to největší
#   okno". A four-metre screen in the control room wants a four-digit number
#   written a foot high and the old 40 px could not do it;
#
#   the floor — the window has no minimum size any more, so a clamp at 10 px
#   would be the thing stopping a small window from fitting its own list.
#
# 750 is 1.5 x 500, which is `FIT_MARK_RATIO` at the text's ceiling: a value
# the fit works out must never be clipped by its own bound.
LOOK_LIMITS = {"size_px": (8, 500), "mark_px": (8, 750)}

# What "Fit everything to the window" works the two numbers out to be. The
# verdict square is one and a half times the text beside it — "velikost
# čtverečků bude třeba 1,5x větší než text s údaji", 1.10.2026, and it is close
# to the pair the program has always started with (19 px of text, 30 px of
# square). Fixed, not a setting: one more box to explain for a proportion
# nobody wants to argue with.
FIT_MARK_RATIO = 1.5

# And what the word inside a hall square comes out as, against the square's own
# side. The hall's NAME is already drawn at 0.26 of the side by
# `ls_ui.HallSquare`, so the word under it has to be plainly subordinate to it.
FIT_WORD_FRAC = 0.10


# ── where the five hall squares sit ──────────────────────────────────────────
# The squares used to be five equal cells in a row and nothing could be done
# about it. They are now placed BY HAND, the way Image Slider's cameras are: a
# board on the Settings tab, each square dragged and resized freely, the result
# stored as fractions of the band so it survives a resized window.
#
# `auto` is the same flag `cam_layouts.json` carries and it means the same
# thing: True and the stored rectangles are ignored and the program arranges the
# squares itself (`auto_hall_tiles`), False and they are used verbatim. The
# first drag turns it off; "Arrange them again" turns it back on. Without the
# flag there is no way back to the even row except editing the file.
#
# `fit`: the operator sizes the window, and the program then WORKS OUT the four
# numbers — the band, the text, the verdict square and the word — so that the
# squares and the list fill that window with no strip of nothing anywhere. Said
# on 1.10.2026: "user si nastaví velikost okna, pak klikne na fit everything to
# the window a script naškáluje 'tabulku' s textem a haly tak, aby se to tam
# vlezlo bez nějakých větších mezer".
#
# IT WRITES WHAT IT WORKED OUT BACK INTO THIS DOCUMENT. That is the whole
# difference from the first attempt, which multiplied the four numbers by a
# scale at drawing time and left the document alone: the Settings tab then said
# "Height of the band 400 px" over a band 253 px tall, and the complaint was
# exactly that — "zatím to absolutně neodpovídá". A number on screen that is
# not the number being used is worse than no number.
#
# `manual` is what the operator last typed by hand, kept so that switching the
# fit off puts his own sizes back. In the document rather than in memory, so it
# survives a restart.
HALLS_DEFAULTS = {"auto": True, "band_px": 150, "show_word": True,
                  "word_px": 11, "layout": {}, "fit": True, "manual": {},
                  "band_frac": 0.0, "single": False}

# `single`: ONE square instead of five, whose name follows the hall being shot
# into, with the list beside it rather than under it. Asked for on 1.10.2026 —
# "pro halu user vidí jen jeden čtvereček a jeho popis se bude automaticky
# měnit v závislosti na tom, kam střílíme". In this mode `band_px` is the
# square's SIDE, so the same box and the same fit/manual machinery serve both.

# Between the one square and the list beside it, in pixels.
SINGLE_GAP_PX = 24

# The smallest text the fit lets the squares squeeze the list down to. The
# squares are as big as they can be while the list beside or under them still
# fits at this size; only when the window is too small for even that does the
# text go smaller, and only below `LOOK_LIMITS`' floor does anything run past
# the window's edge (user, 12.10.2026: "co největší tak, aby se tam vlezl i
# text.. pokud se už text nevleze, tak až teprve tehdy se bude moct text
# přepisovat přes hranu"). The program's own starting text size.
FIT_TEXT_MIN_PX = 19

# The band is the strip the squares live in. Wide bounds, for the reason
# `LOOK_LIMITS` has them: the window may be a wall-sized display or a corner of
# a laptop, and the fit has to be able to land anywhere in between without its
# answer being clipped by a bound.
#
# The floor is 1 px and that is deliberate. A floor of, say, 40 would be the
# document's repair pulling the band back UP on a window only 200 px tall, and
# then "Height of the band" would disagree with the screen again — which is the
# one thing this whole change exists to stop. A silly value typed by hand is
# visible the instant it is typed and is undone the same way.
HALLS_LIMITS = {"band_px": (1, 2000), "word_px": (6, 400)}

# The keys `manual` may hold, and where each one belongs. Two live in `look` and
# two in `halls`, which is why the fit has to be handed both.
MANUAL_KEYS = ("size_px", "mark_px", "band_px", "word_px")

# A square may not be dragged out of the band, and may not be shrunk to nothing:
# a tile smaller than this cannot be grabbed again to make it bigger.
TILE_MIN_FRAC = 0.04

# The gap the automatic arrangement leaves between two squares, in band pixels.
AUTO_GAP_PX = 10

# What share of the height a HAND-MADE arrangement's band keeps while the fit
# is on, before anybody has dragged anything in this version of the file. The
# real figure is `halls["band_frac"]`, written by the board the moment a square
# is moved; this is only the fallback for a file saved before that key existed.
HAND_BAND_FRAC = 0.30


def auto_hall_tiles(width_px, height_px):
    """The even row: five squares, centred, `AUTO_GAP_PX` apart.

    This is what the program did before the squares could be moved, and it is
    what `auto` means. Worked out from the band's REAL pixel size rather than
    written down as fractions, because a square has to be square: the fractions
    that make one are different on every window width.

    THE CENTRING IS NOT WHAT CLOSES THE GAP. A square is `min(h, width share)`
    wide, so on a band taller than the width allows there is a strip of nothing
    above and below it, and that strip was the complaint of 1.10.2026 — "mezi
    tabama a linkou oddělující text od čtverečků pro haly bytečně moc volného
    místa". What closes it is `ov_t.OverviewTab._fit_to_window`, which hands the
    band exactly the side it works out, so `y0` comes out zero and there is
    nothing left to centre. With the fit switched off `band_needed` does the
    same: the band is never placed taller than the side, however big "Height
    of the band" is, so the room goes to the list instead of round the squares.
    """
    w = max(1.0, float(width_px))
    h = max(1.0, float(height_px))
    n = len(HALLS)
    gap = AUTO_GAP_PX
    side = max(8.0, min(h, (w - (n - 1) * gap) / n))
    total = n * side + (n - 1) * gap
    x0 = max(0.0, (w - total) / 2.0)
    y0 = max(0.0, (h - side) / 2.0)
    out = {}
    for i, hall in enumerate(HALLS):
        out[hall] = ((x0 + i * (side + gap)) / w, y0 / h, side / w, side / h)
    return out


def _clean_tile(value):
    """One [x, y, w, h] made safe, or None if it is not a rectangle at all."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x, y, w, h = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    m = TILE_MIN_FRAC
    w = max(m, min(1.0, w))
    h = max(m, min(1.0, h))
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return [x, y, w, h]


def _clean_halls(halls):
    """The band's settings made safe, in place. Anything odd goes to default."""
    for key in ("band_px", "word_px"):
        low, high = HALLS_LIMITS[key]
        try:
            value = int(round(float(halls.get(key))))
        except (TypeError, ValueError):
            value = HALLS_DEFAULTS[key]
        halls[key] = max(low, min(high, value))
    halls["show_word"] = bool(halls.get("show_word",
                                        HALLS_DEFAULTS["show_word"]))
    halls["fit"] = bool(halls.get("fit", HALLS_DEFAULTS["fit"]))
    halls["single"] = bool(halls.get("single", HALLS_DEFAULTS["single"]))
    raw = halls.get("layout")
    layout = {}
    if isinstance(raw, dict):
        for hall in HALLS:
            tile = _clean_tile(raw.get(hall))
            if tile is not None:
                layout[hall] = tile
    halls["layout"] = layout
    # A hand-made arrangement that is missing a hall cannot be used — the
    # missing square would have nowhere to go — so it falls back to automatic
    # rather than vanishing from the first tab.
    halls["auto"] = bool(halls.get("auto", HALLS_DEFAULTS["auto"])) \
        or len(layout) != len(HALLS)
    halls["manual"] = _clean_manual(halls.get("manual"))
    # 0 means "nobody has told us", which is what the fallback is for.
    try:
        frac = float(halls.get("band_frac") or 0.0)
    except (TypeError, ValueError):
        frac = 0.0
    halls["band_frac"] = 0.0 if frac <= 0.0 else min(0.92, max(0.08, frac))
    return halls


def _clean_manual(raw):
    """What the operator last typed by hand, made safe. Partial is allowed.

    An empty dictionary means he has never typed any of them, which is the
    first-run state: switching the fit off then leaves whatever the fit worked
    out, because there is nothing else to go back to.

    A key that is missing stays missing rather than being filled from the
    defaults — "he never set the band" and "he set the band to the default
    value" are different facts, and only the second one should survive the fit
    being switched off.
    """
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in MANUAL_KEYS:
        if key not in raw:
            continue
        low, high = (LOOK_LIMITS.get(key) or HALLS_LIMITS.get(key))
        try:
            value = int(round(float(raw[key])))
        except (TypeError, ValueError):
            continue
        out[key] = max(low, min(high, value))
    return out


def manual_sizes(look, halls):
    """The four numbers as they are RIGHT NOW, ready to be remembered."""
    return {"size_px": int(look["size_px"]), "mark_px": int(look["mark_px"]),
            "band_px": int(halls["band_px"]), "word_px": int(halls["word_px"])}


def apply_manual(look, halls, manual):
    """Put remembered sizes back. Returns True if anything actually changed."""
    changed = False
    for key, value in (manual or {}).items():
        target = look if key in LOOK_LIMITS else halls
        if key in target and int(target[key]) != int(value):
            target[key] = int(value)
            changed = True
    return changed


def halls_of(cfg):
    """The band's own settings, filled in and written back if they were missing.

    Handed out as the live dictionary, the way `look_of` hands out the look: the
    Settings tab edits it in place and the Overview reads it on every reload.
    """
    halls = cfg.get("halls")
    if not isinstance(halls, dict):
        halls = {k: (dict(v) if isinstance(v, dict) else v)
                 for k, v in HALLS_DEFAULTS.items()}
        cfg["halls"] = halls
    return _clean_halls(halls)


def hall_tiles(halls, width_px, height_px):
    """Where the five squares go, in fractions: by hand, or arranged for us."""
    if not halls.get("auto") and len(halls.get("layout") or {}) == len(HALLS):
        return {h: tuple(halls["layout"][h]) for h in HALLS}
    return auto_hall_tiles(width_px, height_px)


# ── fitting the first tab into the window ────────────────────────────────────
# The rule lives here, with every other rule, and not in the drawing code: it
# is arithmetic over a handful of numbers and it is the thing most likely to be
# got wrong, so it has to be testable without building a window.
#
# The one thing it CANNOT do here is measure text. How tall a row is depends on
# the typeface, and only Qt knows that — so the caller passes two callables,
# `row_h(t)` and `row_w(t)`, which measure a row at a text size of `t`. `ov_t`
# builds them out of `QFontMetrics`; a test can pass arithmetic ones.

def band_side_cap(band_w):
    """The widest a hall square can be and still be square, in the even row."""
    n = len(HALLS)
    return max(8.0, (float(band_w) - (n - 1) * AUTO_GAP_PX) / n)


def band_needed(halls, band_w, band_h):
    """How tall the band really has to be for its squares, in pixels.

    In the even row a square is never wider than the width allows, so a band
    taller than that side is nothing but empty strip above and below — and that
    strip was the complaint of 1.10.2026: the window was made taller and the gap
    round the squares grew while the list of energies stayed hidden. So the band
    never takes more than its side, and the rest goes to the list.

    A hand-made arrangement is fractions of the band, whatever shape it was
    dragged into, so it has nothing to trim and gets what it asked for.
    """
    if not halls.get("auto") and len(halls.get("layout") or {}) == len(HALLS):
        return int(band_h)
    # Rounded the way `fit_sizes` rounds its band, or the two disagree by one.
    return int(round(band_side_cap(band_w)))


def _largest(low, high, fits):
    """The largest integer in [low, high] that `fits`, by bisection.

    Bisection rather than stepping down from an algebraic guess, for two
    reasons. It is bounded — ten measurements for a range of five hundred,
    whatever the window — and it needs no fudge factor: the first attempt at
    this shaved three per cent off an algebraic answer to cover the fact that a
    font's height does not grow in step with its pixel size, which is a guess
    about the error rather than a measurement of it.

    `fits` is ALMOST monotone in `t` — a bigger text is never narrower or
    shorter — but a font's height is a quantised step function of its pixel
    size, so a size one bigger can measure the same and the step can land
    anywhere. Bisection can therefore stop one short of the boundary or one
    past it, which is why the answer is walked afterwards: DOWN until it really
    fits, which is the half that matters, and then UP while the next one still
    does. Both walks are a step or two, because bisection has already landed on
    the boundary.

    Returns `low` when nothing fits — something has to be drawn, and the floor
    is the least bad thing to draw.
    """
    low, high = int(low), int(high)
    if not fits(low):
        return low
    lo, hi, best = low, high, low
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    while best > low and not fits(best):
        best -= 1
    while best < high and fits(best + 1):
        best += 1
    return best


def _text_floor(low, fits):
    """The text size the squares must leave room for: `FIT_TEXT_MIN_PX` when
    the rows fit at it with no square at all, else the largest size that does,
    else `low`."""
    if fits(FIT_TEXT_MIN_PX):
        return FIT_TEXT_MIN_PX
    return _largest(low, FIT_TEXT_MIN_PX, fits)


def fit_sizes(band_w, list_w, avail, n_rows, row_h, row_w,
              auto=True, band_frac=None):
    """The four numbers that fill a window. No Qt in here.

        band_w      the width the BAND has
        list_w      the width one ROW has, which is a little less: the list is
                    inside a scroller and the scrollbar's width is counted
                    whether it is showing or not
        avail       the height they share, once every fixed pixel — margins,
                    spacings, the separator, the per-row padding — has been
                    taken off. The CALLER works that out, because the caller is
                    the file those numbers are set in.
        n_rows      how many rows the list is showing
        row_h(t)    one row's height at text size `t`
        row_w(t)    one row's width at text size `t`
        auto        the even row of five, or a hand-made arrangement
        band_frac   the share of the height a HAND-MADE band keeps. Ignored
                    when `auto`, where the width decides instead.

    Returns `(size_px, mark_px, band_px, word_px)`; `size_px` and `mark_px`
    come back `None` when there is no row to measure. Everything else is
    already inside `LOOK_LIMITS` / `HALLS_LIMITS`.
    """
    low, high = LOOK_LIMITS["size_px"]
    n = max(0, int(n_rows))
    band_w = max(1, int(band_w))
    list_w = max(1, int(list_w))
    avail = max(0, int(avail))

    # The tallest a square may be. In the even row the WIDTH decides it as
    # often as the height does — five squares side by side on a narrow window
    # run out of width long before they run out of height, and a band taller
    # than that is the strip of nothing this whole function exists to close.
    #
    # A hand-made arrangement has no square to aim at: its tiles are fractions
    # of the band and are whatever shape he dragged them. It keeps its SHARE of
    # the height instead, so his arrangement grows with the window in one piece
    # rather than sitting at one fixed height in the corner of a big screen.
    if auto:
        cap = band_side_cap(band_w)
    else:
        share = band_frac if band_frac else HAND_BAND_FRAC
        cap = max(1.0, avail * min(0.92, max(0.08, float(share))))

    if n == 0:
        # Nothing watched: the squares take the window. A band left at 150 px
        # over an empty page was the other half of the empty-space complaint.
        # No text size is returned — there is no row to measure, so there is no
        # information, and parking the ceiling here because "there is room"
        # would ambush him the moment he ticked Show.
        band = min(cap, float(avail))
        size = mark = None
    else:
        # The squares take as much as they may — but never so much that the
        # list cannot show its rows at `FIT_TEXT_MIN_PX`, or at the floor when
        # the window is too small even for that.
        floor = _text_floor(low, lambda t: (n * row_h(t) <= avail
                                            and row_w(t) <= list_w))
        band = min(cap, max(0.0, avail - n * row_h(floor)))
        size = _largest(low, high, lambda t: (n * row_h(t) <= avail - band
                                              and row_w(t) <= list_w))
        # The text hit the width, or its own ceiling, before it used up the
        # height. The room it did not take goes back to the squares rather than
        # being left as a strip of nothing. It cannot go past `cap`, and in the
        # even row `cap` IS the square's side, so what is left over after that
        # lands under the last row — which is where leftover room belongs.
        band = min(cap, max(band, avail - n * row_h(size)))
        mark_lo, mark_hi = LOOK_LIMITS["mark_px"]
        mark = max(mark_lo, min(mark_hi, int(round(size * FIT_MARK_RATIO))))

    band_lo, band_hi = HALLS_LIMITS["band_px"]
    band_i = max(band_lo, min(band_hi, int(round(band))))
    word_lo, word_hi = HALLS_LIMITS["word_px"]
    word = max(word_lo, min(word_hi, int(round(band_i * FIT_WORD_FRAC))))
    return size, mark, band_i, word


def fit_single(width, square_h, list_h, list_spare, n_rows, row_h, row_w):
    """The four numbers for the one-square mode. No Qt in here.

        width       the tab's inner width, shared by the square and the list
        square_h    the height the square may take
        list_h      the height the rows share, once their fixed pixels are off
        list_spare  pixels of the list's width that are never a row's — the
                    scrollbar, counted always, as `fit_sizes`' caller does
        n_rows      how many rows the list is showing
        row_h(t), row_w(t)   one row's height and width at text size `t`

    THE SQUARE FIRST, AS BIG AS THE TEXT LETS IT (12.10.2026). The square is
    as tall as the height allows, and only as narrow as it must be for the
    list to fit beside it at `FIT_TEXT_MIN_PX` (or, on a window too small for
    that, at the largest size that fits at all). Then the text grows into
    whatever room is left beside it. Before, the text came first and the square
    got at most 35 % of the width and usually half of that — "the big square is
    too small".

    Returns `(size_px, mark_px, side_px, word_px)`; `size_px` and `mark_px` are
    `None` when there is no row to measure.
    """
    low, high = LOOK_LIMITS["size_px"]
    n = max(0, int(n_rows))
    width = max(1, int(width))
    square_h = max(1, int(square_h))
    list_h = max(0, int(list_h))
    beside = width - SINGLE_GAP_PX - list_spare     # square + one row

    def room(s):
        return beside - s

    if n == 0:
        side = float(min(square_h, width))
        size = mark = None
    else:
        floor = _text_floor(low, lambda t: (n * row_h(t) <= list_h
                                            and row_w(t) <= beside - 1))
        side = max(1.0, min(float(square_h), beside - row_w(floor)))
        size = _largest(low, high, lambda t: (n * row_h(t) <= list_h
                                              and row_w(t) <= room(side)))
        mark_lo, mark_hi = LOOK_LIMITS["mark_px"]
        mark = max(mark_lo, min(mark_hi, int(round(size * FIT_MARK_RATIO))))

    band_lo, band_hi = HALLS_LIMITS["band_px"]
    side_i = max(band_lo, min(band_hi, int(round(side))))
    word_lo, word_hi = HALLS_LIMITS["word_px"]
    word = max(word_lo, min(word_hi, int(round(side_i * FIT_WORD_FRAC))))
    return size, mark, side_i, word


def _clean_look(look):
    """The four numbers made safe, in place. Anything odd goes to its default."""
    family = look.get("family")
    look["family"] = (family.strip() if isinstance(family, str) and family.strip()
                      else LOOK_DEFAULTS["family"])
    for key in ("size_px", "mark_px"):
        low, high = LOOK_LIMITS[key]
        try:
            value = int(round(float(look.get(key))))
        except (TypeError, ValueError):
            value = LOOK_DEFAULTS[key]
        look[key] = max(low, min(high, value))
    look["bold"] = bool(look.get("bold", LOOK_DEFAULTS["bold"]))
    return look


# ── what Maximise does ───────────────────────────────────────────────────────
# Asked for on 1.10.2026: "přidej do nastavení možnost, kdy si user vybere
# velikost okna.. když user potom klikne na maximalizaci, tak se to maximalizuje
# do velikosti okna, kterou si nastavil". Switched on, the Maximise button makes
# the window `w` x `h` in the top left corner of its screen; switched off it
# fills the screen as any window does. The size is the WHOLE window, frame and
# title bar included, in the same pixels the Settings tab's boxes show.
#
# `x` and `y` are WHERE: how far the window's top left corner is from the top
# left corner of the screen's free area (the screen minus the taskbar). Added
# the same day — "vždy to hodí ten program do nějakého rohu... aby si to
# pamatovalo i pozici". Counted from the free area of whichever screen the
# window is maximised on rather than in desktop coordinates, so the window
# lands in the same place on any screen, and a screen that has been unplugged
# cannot send it off into nowhere.
WINDOW_MAX_DEFAULTS = {"on": False, "w": 1280, "h": 720, "x": 0, "y": 0}
WINDOW_MAX_LIMITS = (200, 10000)
WINDOW_POS_LIMITS = (0, 10000)


def window_max_of(cfg):
    """The Maximise size, filled in and repaired, as the live dictionary."""
    wm = cfg.get("window_max")
    if not isinstance(wm, dict):
        wm = dict(WINDOW_MAX_DEFAULTS)
        cfg["window_max"] = wm
    wm["on"] = bool(wm.get("on", WINDOW_MAX_DEFAULTS["on"]))
    for key in ("w", "h", "x", "y"):
        low, high = WINDOW_MAX_LIMITS if key in "wh" else WINDOW_POS_LIMITS
        try:
            value = int(round(float(wm.get(key))))
        except (TypeError, ValueError):
            value = WINDOW_MAX_DEFAULTS[key]
        wm[key] = max(low, min(high, value))
    return wm


def max_rect(want_x, want_y, want_w, want_h, work_w, work_h):
    """Where a maximised window goes: `(x, y, w, h)` from the work area's top
    left corner. Never bigger than the work area and never hanging over its
    edge: the size is cut first, then the window is slid back in, so a place
    remembered on a bigger screen still keeps the whole window in view. No Qt,
    no Windows — the window does the units."""
    w = max(1, min(int(want_w), int(work_w)))
    h = max(1, min(int(want_h), int(work_h)))
    x = max(0, min(int(want_x), int(work_w) - w))
    y = max(0, min(int(want_y), int(work_h) - h))
    return x, y, w, h


# The deformable mirror's set-up, read off the Remote Desktop window on OPR1
# (`ls_dm`). Per PC, like everything in this file: only the PC that has that
# window open switches `write` on. `title` is the text the window's title
# carries — the remote PC's address.
# `every_s` is how often that PC looks (asked 9.10.2026: "něco jako 2s nebo 5s
# nebo 3s"); it is written into the state file, and every other PC reads the
# file that often too. All choices stay well under `ls_dm.STALE_AFTER_S`.
DM_EVERY_MIN_S = 1
DM_EVERY_MAX_S = 10
DM_DEFAULTS = {"write": False, "title": "192.168.1.107", "every_s": 5}


def dm_every(value):
    """A look interval from a file or a state document, repaired: whole
    seconds from DM_EVERY_MIN_S to DM_EVERY_MAX_S, else the default."""
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return DM_DEFAULTS["every_s"]
    return max(DM_EVERY_MIN_S, min(DM_EVERY_MAX_S, value))


def dm_of(cfg):
    """The DM reading's settings, filled in and repaired: the live dictionary."""
    dm = cfg.get("dm")
    if not isinstance(dm, dict):
        dm = dict(DM_DEFAULTS)
        cfg["dm"] = dm
    dm["write"] = bool(dm.get("write", DM_DEFAULTS["write"]))
    title = dm.get("title")
    dm["title"] = (title.strip() if isinstance(title, str) and title.strip()
                   else DM_DEFAULTS["title"])
    dm["every_s"] = dm_every(dm.get("every_s"))
    return dm


def program_dir():
    """The folder the program lives in — next to the exe once it is built."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def config_dir():
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME


def config_path():
    return config_dir() / CONFIG_NAME


def default_config():
    """A first run: the gate valves we know, and nothing watched yet.

    Nothing else is invented. A hall whose valve channels are not known yet is
    left empty, and its square comes up grey rather than white — "we do not
    know" is not "we are not shooting".
    """
    return {"version": CONFIG_VERSION,
            "shooting": {h: default_gate(h) for h in HALLS},
            "rows": [],
            "scanning": False,
            "look": dict(LOOK_DEFAULTS),
            "halls": {k: (dict(v) if isinstance(v, dict) else v)
                      for k, v in HALLS_DEFAULTS.items()},
            "shots": {"reset_s": SHOT_RESET_S_DEFAULT,
                      "size_px": SHOT_SIZE_PX_DEFAULT,
                      "bold": SHOT_BOLD_DEFAULT},
            "active_hall": HALLS[0]}


def shots_of(cfg):
    """The shot counter's settings, filled in and repaired: the live dictionary.

    `reset_s` — how long without a shot before the count starts again — held
    to `SHOT_RESET_LIMITS`; `size_px` and `bold`, how the "Shot counter: 12"
    line above the list on the Overview is written.
    """
    shots = cfg.get("shots")
    if not isinstance(shots, dict):
        shots = {}
        cfg["shots"] = shots
    for key, default, (low, high) in (
            ("reset_s", SHOT_RESET_S_DEFAULT, SHOT_RESET_LIMITS),
            ("size_px", SHOT_SIZE_PX_DEFAULT, SHOT_SIZE_LIMITS)):
        try:
            value = int(round(float(shots.get(key, default))))
        except (TypeError, ValueError):
            value = default
        shots[key] = max(low, min(high, value))
    shots["bold"] = bool(shots.get("bold", SHOT_BOLD_DEFAULT))
    return shots


def look_of(cfg):
    """The list's own look, filled in and written back if it was missing.

    Handed out as the live dictionary, the way `gate_for` hands out a gate: the
    Settings tab edits it in place and the Overview reads it on every reload.
    """
    look = cfg.get("look")
    if not isinstance(look, dict):
        look = dict(LOOK_DEFAULTS)
        cfg["look"] = look
    return _clean_look(look)


def _repair_misplaced_formulas(rows):
    """A formula that was typed into the CHANNEL box, put where it belongs.

    The shape, found in a real settings file on 30.9.2026:

        {"id": 4, "pv": "C*0.749", "name": "SBW4 Compressed", "expr": ""}

    A formula row whose expression is empty and whose channel name cannot be a
    channel name. An archived channel here always carries a colon — every one of
    them is `L3-…:Something` — so a `pv` without one, on a row that is already
    known to be a formula, is the expression in the wrong box. The program had
    been asking the archiver for a channel literally called `C*0.749` once a
    second, and "Reading now" was empty with nothing to say why.

    Done after every row is built, because the letters are the rows' POSITIONS:
    `expr_from_display` can only resolve `C` once the whole list is there.
    """
    for row in rows:
        if not is_formula_row(row) or is_formula(row):
            continue
        typed = (row.get("pv") or "").strip()
        if not typed or ":" in typed:
            continue
        expr, bind = expr_from_display(typed, rows)
        row["expr"], row["bind"], row["pv"] = expr, bind, ""


def normalise(cfg):
    """Make any document safe to work with, whatever was in the file.

    Anything unreadable is replaced with the default for that one field rather
    than throwing the whole file away: a settings file with one bad limit in it
    still holds forty good ones.
    """
    out = default_config()
    if not isinstance(cfg, dict):
        return out
    shooting = cfg.get("shooting")
    if isinstance(shooting, dict):
        for hall in HALLS:
            raw = shooting.get(hall)
            if raw is None:
                continue                # the hall keeps its default
            if isinstance(raw, str):
                # The shape before the valve had two channels: one name, which
                # was the open switch.
                name = raw.strip()
                if _DEAD_GATE_NAMES.get(name) == hall:
                    name = DEFAULT_GATES[hall]["open"]
                out["shooting"][hall] = {"open": name}
                continue
            if not isinstance(raw, dict):
                continue
            # `closed`, `on` and `shoot_when` were settings once and are not
            # any more; they are dropped here rather than carried, so the file
            # never holds a rule the program does not follow.
            name = raw.get("open")
            # A saved EMPTY name is a name the operator cleared, and it must
            # stay cleared — a default put back here would keep resurrecting a
            # channel they deliberately took out.
            name = str(name).strip() if isinstance(name, str) else ""
            if _DEAD_GATE_NAMES.get(name) == hall:
                name = DEFAULT_GATES[hall]["open"]
            out["shooting"][hall]["open"] = name
    rows = []
    seen_ids = set()
    old_scan_ticked = False
    for raw in (cfg.get("rows") or []):
        if not isinstance(raw, dict):
            continue
        try:
            row_id = int(raw.get("id"))
        except (TypeError, ValueError):
            row_id = 0
        if row_id <= 0 or row_id in seen_ids:
            row_id = max(seen_ids or [0]) + 1
        seen_ids.add(row_id)
        row = new_row(row_id, str(raw.get("pv") or ""), str(raw.get("name") or ""),
                      str(raw.get("unit") or ""))
        expr = raw.get("expr")
        if raw.get("kind") == SHOT_KIND:
            # The shot counter: one on the list at most, and it reads nothing.
            if any(is_shot_row(r) for r in rows):
                continue
            row["kind"] = SHOT_KIND
            row["pv"] = ""
            row["unit"] = ""
            # Its old default name, from before it left the list (5.10.2026).
            if row["name"].strip() == "Shots":
                row["name"] = SHOT_ROW_NAME
        elif raw.get("kind") == DM_KIND:
            # DM settings: one on the list at most, it reads no channel.
            if any(is_dm_row(r) for r in rows):
                continue
            row["kind"] = DM_KIND
            row["pv"] = ""
            row["unit"] = ""
        elif isinstance(expr, str):
            # The key is KEPT even when the expression is empty: it is what says
            # "this row is a formula" before anything has been typed into it.
            # See `is_formula_row` for what dropping it used to cost.
            row["expr"] = expr.strip()
            if row["expr"]:
                row["pv"] = ""      # a formula has no channel of its own
            bind = {}
            for letter, rid in (raw.get("bind") or {}).items():
                try:
                    bind[str(letter)] = int(rid)
                except (TypeError, ValueError):
                    continue        # a letter we cannot place stays unbound
            row["bind"] = bind
        limits = raw.get("limits")
        # A file from before Show became one switch had it per hall. Ticked in
        # ANY hall comes forward as ticked, so nothing the operator was watching
        # quietly disappears on the first run of the new version; unticked
        # everywhere stays unticked. Untick it once now and it is off in all
        # five, which is the point of the change.
        per_hall = []
        if isinstance(limits, dict):
            for hall in HALLS:
                entry = limits.get(hall)
                if not isinstance(entry, dict):
                    continue
                target = row["limits"][hall]
                target["min"] = parse_level(entry.get("min"))
                target["max"] = parse_level(entry.get("max"))
                if "show" in entry:
                    per_hall.append(bool(entry.get("show")))
        shown = raw.get("show")
        if shown is not None:
            row["show"] = bool(shown)
        elif per_hall:
            row["show"] = any(per_hall)
        row["alarm"] = bool(raw.get("alarm", True))
        row["scan_min"] = parse_level(raw.get("scan_min"))
        row["scan_max"] = parse_level(raw.get("scan_max"))
        # The per-row Scanning tick of 8.10.2026: a row that was scanning when
        # the file was written turns the one Overview switch on, so a scan
        # going on does not drop back to Min and Max on the first run.
        if bool(raw.get("scan", False)) and has_scan_limits(row):
            old_scan_ticked = True
        if is_builtin_row(row):
            row["scan_min"] = row["scan_max"] = None
            # The shot counter has no limits (5.10.2026): a number of shots is
            # never right or wrong, and it has no verdict square. DM settings
            # has none either: its verdict is `dm_judge`'s.
            for hall in HALLS:
                row["limits"][hall].update({"min": None, "max": None})
        rows.append(row)
    _repair_misplaced_formulas(rows)
    # The shot counter is always on the list (5.10.2026): a file from before
    # that, or one it was removed from, gets it back at the bottom, shown. Its
    # Show tick is what hides it.
    if not any(is_shot_row(r) for r in rows):
        rows.append(new_shot_row(next_id(rows)))
    # DM settings likewise (8.10.2026), but UNTICKED: the Overview gains the
    # line only when somebody ticks its Show.
    if not any(is_dm_row(r) for r in rows):
        rows.append(new_dm_row(next_id(rows)))
    out["rows"] = rows
    scanning = cfg.get("scanning")
    out["scanning"] = (bool(scanning) if isinstance(scanning, bool)
                       else old_scan_ticked)
    look = cfg.get("look")
    if isinstance(look, dict):
        out["look"] = _clean_look({**LOOK_DEFAULTS, **look})
    halls = cfg.get("halls")
    if isinstance(halls, dict):
        out["halls"] = _clean_halls({**HALLS_DEFAULTS, **halls})
    shots = cfg.get("shots")
    if isinstance(shots, dict):
        out["shots"] = dict(shots)
        shots_of(out)
    hall = cfg.get("active_hall")
    out["active_hall"] = hall if hall in HALLS else HALLS[0]
    geom = cfg.get("window_geometry")
    if isinstance(geom, list) and len(geom) == 4 \
            and all(isinstance(v, (int, float)) for v in geom):
        out["window_geometry"] = [int(v) for v in geom]
    wm = cfg.get("window_max")
    if isinstance(wm, dict):
        out["window_max"] = dict(wm)
        window_max_of(out)
    dm = cfg.get("dm")
    if isinstance(dm, dict):
        out["dm"] = dict(dm)
        dm_of(out)
    return out


def load_config(path=None):
    """(settings, problem). A missing file is a first run, not a problem."""
    p = Path(path) if path else config_path()
    # The defaults go through `normalise` too, which is what puts the shot
    # counter on the list.
    if not p.exists():
        return normalise(default_config()), None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return (normalise(default_config()),
                f"{p.name} could not be read ({exc}) — starting from defaults")
    if not isinstance(data, dict):
        return (normalise(default_config()),
                f"{p.name} does not hold settings — starting from defaults")
    return normalise(data), None


def save_config(cfg, path=None):
    """Write the settings, atomically. Returns a problem, or None.

    A temporary file beside it and a rename, which either happens or does not:
    a half-written file would cost the operator every limit they ever set.
    """
    p = Path(path) if path else config_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
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


# ─────────────────────────────────────────────────────────────────────────────
# Saved settings: named sets on the share, one per operator
# ─────────────────────────────────────────────────────────────────────────────
# Asked for on 12.10.2026: "abych měl možnost nahrát více nastavení podle
# toho, jaký user to bude ovládat.. dropdown menu těch nastavení, ze kterých si
# vyberu, klik na import a voala.. všechny nastavení se změnili". The sets
# live in `settings_sets.json` beside `dm_setups.json` on the share, so every PC
# sees the same list; the Settings tab reads and writes it on a thread.
#
# A set holds PARTS of the settings document, the operator ticks which ones
# are saved and which are loaded. The DM writer switch (`dm.write`) is never
# in a set: it says which PC reads the mirror, and loading somebody's set on
# a second PC must not make two writers. The saved DM set-ups and their
# High / Low ticks are on the share already and need no copy.

SETS_NAME = "settings_sets.json"

# (key, caption, top-level keys of the settings document). "dm" is handled on
# its own, see `_DM_SET_KEYS`.
SET_PARTS = (
    ("channels", "Channels and limits",
     ("rows", "shooting", "scanning", "active_hall")),
    ("look", "Look (text, squares)", ("look", "halls")),
    ("shots", "Shot counter", ("shots",)),
    ("dm", "DM reading", ()),
    ("window", "Window size and place", ("window_max", "window_geometry")),
)
SET_PART_KEYS = tuple(p[0] for p in SET_PARTS)
SET_PART_CAPTIONS = {p[0]: p[1] for p in SET_PARTS}
# Ticked when the tab opens: everything but where the window sits, which is a
# matter of the PC's screen rather than of who is operating.
SET_PARTS_DEFAULT = ("channels", "look", "shots", "dm")
_DM_SET_KEYS = ("title", "every_s")


def settings_parts(cfg, parts):
    """The chosen parts of a settings document, deep-copied, for a set."""
    import copy
    out = {}
    for key, _caption, doc_keys in SET_PARTS:
        if key not in parts:
            continue
        if key == "dm":
            dm = dm_of(copy.deepcopy(cfg))
            out["dm"] = {k: dm[k] for k in _DM_SET_KEYS}
            continue
        for doc_key in doc_keys:
            if doc_key in cfg:
                out[doc_key] = copy.deepcopy(cfg[doc_key])
    return out


def parts_in(settings):
    """Which parts a saved set holds, in `SET_PARTS` order."""
    if not isinstance(settings, dict):
        return []
    held = []
    for key, _caption, doc_keys in SET_PARTS:
        if key == "dm":
            if isinstance(settings.get("dm"), dict):
                held.append(key)
        elif any(k in settings for k in doc_keys):
            held.append(key)
    return held


def apply_settings_parts(cfg, settings, parts):
    """A NEW settings document: `cfg` with the chosen parts of a saved set
    laid over it, repaired by `normalise`. `cfg` itself is not touched.
    A part the set does not hold leaves that part as it was."""
    import copy
    out = copy.deepcopy(cfg)
    if not isinstance(settings, dict):
        return normalise(out)
    for key, _caption, doc_keys in SET_PARTS:
        if key not in parts:
            continue
        if key == "dm":
            saved = settings.get("dm")
            if isinstance(saved, dict):
                dm = dm_of(out)
                for k in _DM_SET_KEYS:
                    if k in saved:
                        dm[k] = saved[k]
            continue
        for doc_key in doc_keys:
            if doc_key in settings:
                out[doc_key] = copy.deepcopy(settings[doc_key])
    return normalise(out)


def clean_sets(raw):
    """{name: {"settings", "saved", "by"}} out of a file, bad entries dropped."""
    out = {}
    if not isinstance(raw, dict):
        return out
    items = raw.get("sets") if isinstance(raw.get("sets"), dict) else {}
    for name, entry in items.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(entry, dict) \
                or not isinstance(entry.get("settings"), dict):
            continue
        out[name.strip()] = {"settings": entry["settings"],
                             "saved": str(entry.get("saved") or ""),
                             "by": str(entry.get("by") or "")}
    return out


def sets_document(sets):
    return {"version": 1, "sets": sets}


def _set_entry(settings, now_s=None):
    import datetime
    now = time.time() if now_s is None else now_s
    return {"settings": settings,
            "saved": datetime.datetime.fromtimestamp(now).isoformat(
                timespec="seconds"),
            "by": socket.gethostname()}


def add_set(sets, name, settings, now_s=None):
    """A copy of `sets` with a new one, or (None, why not)."""
    name = (name or "").strip()
    if not name:
        return None, "Type a name for the settings first."
    if name in sets:
        return None, (f"Settings called \"{name}\" already exist. Pick them "
                      f"and press Save to overwrite them, or type another "
                      f"name.")
    if not settings:
        return None, "Tick at least one part to save."
    out = dict(sets)
    out[name] = _set_entry(settings, now_s)
    return out, None


def overwrite_set(sets, name, settings, now_s=None):
    """A copy of `sets` with `name` saved again, or (None, why not)."""
    if name not in sets:
        return None, f"\"{name}\" is not on the list any more."
    if not settings:
        return None, "Tick at least one part to save."
    out = dict(sets)
    out[name] = _set_entry(settings, now_s)
    return out, None


def rename_set(sets, old, new):
    """A copy of `sets` with `old` called `new`, order kept, or (None, why)."""
    new = (new or "").strip()
    if old not in sets:
        return None, f"\"{old}\" is not on the list any more."
    if not new:
        return None, "Type the new name first."
    if new == old:
        return None, None
    if new in sets:
        return None, f"Settings called \"{new}\" already exist."
    return {(new if n == old else n): e for n, e in sets.items()}, None


def delete_set(sets, name):
    """A copy of `sets` without `name`, or (None, why not)."""
    if name not in sets:
        return None, f"\"{name}\" is not on the list any more."
    return {n: e for n, e in sets.items() if n != name}, None
