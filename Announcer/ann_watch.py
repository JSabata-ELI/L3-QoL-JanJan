"""Announcer — the engine: what is watched, how often, and what state it is in.

Everything on screen renders from here. The tabs hold no verdicts of their own;
they read `state_of(item_id)` and paint it.

READING IS NOT ALERTING
-----------------------
The numbers and the pictures are refreshed whether or not the alarm is armed.
Twice a second while watching, every two seconds while not. That is what makes
the tables useful for setting a limit in the first place — you watch the value
move and then decide — and it means the program never shows a blank table and
calls it "nothing wrong".

What watching changes is only whether a trip RAISES anything.

THE POLL SHAPE, AND THE WEDGE IT AVOIDS
---------------------------------------
Two clocks, two guards, never shared:

    the values   an HTTP round trip to the archiver, on a worker thread
    the pictures a screen grab and a comparison, also on a worker thread

Each one is single-flight, so a slow pass cannot overlap itself. The guard is
NOT a bare boolean, because a bare boolean is how a feature dies until restart:
one pass that never returns leaves the flag set and nothing ever runs again.
This session has paid for that lesson once already. So:

  * the timer ALWAYS ticks, even when it dispatches nothing — it is also the
    heartbeat that notices a pass has gone missing;
  * each pass carries a generation number, and a result whose generation is no
    longer current is dropped rather than applied;
  * a pass still outstanding after several intervals is WRITTEN OFF: logged,
    counted, the flag cleared, and a fresh pass started. The old one cannot be
    cancelled — an HTTP read in a thread never can — so it is abandoned and its
    answer is simply never looked at.

Staleness is measured from our last GOOD read, in elapsed local seconds, which
is a difference and therefore safe. It is never a comparison against an archiver
timestamp: this PC's clock runs about 25 s ahead of the facility. And it is
never "the value stopped changing" — the archiver writes on change only, so a
chiller holding its setpoint publishes nothing for minutes, and that is a
healthy chiller.
"""

from __future__ import annotations

import time

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTimer, Signal

import ann_core as C
import ann_cpva as A
import ann_screen as S

# How long the same failure stays quiet before it is said again. Five minutes:
# long enough that a dead channel does not drown the log, short enough that
# "this has been broken all morning" is still visible in it.
_ERROR_AGAIN_S = 300.0


class ItemState:
    """The last thing known about one watched item."""
    __slots__ = ("state", "text", "value", "read_at", "latched", "fired",
                 "hint", "diff", "size_now", "err_said", "since",
                 "answered_at")

    def __init__(self):
        self.state = C.STATE_UNKNOWN
        self.text = "not read yet"
        self.value = None
        self.read_at = None      # time.monotonic() of the last GOOD read
        # The last read that did not FAIL, whether or not it brought a number
        # back. Not the same thing as `read_at`: a channel written on change
        # only can answer perfectly and still have nothing in the window, and
        # that is a quiet channel, never "no data". Only this one decides the
        # exclamation mark beside the circle.
        self.answered_at = None
        # When this row came into being. A row that has never been read at all
        # has neither time above, and "nothing has answered since the program
        # started" still has to be measurable.
        self.since = time.monotonic()
        self.latched = False     # has this trip already been announced
        self.fired = False       # has it raised the alarm in this watch
        self.hint = None
        self.diff = None
        self.size_now = None
        # (the sentence, when it was said) — see `WatchEngine._say_error`.
        self.err_said = None

    @property
    def age_s(self):
        return None if self.read_at is None else time.monotonic() - self.read_at


# ── the two passes ───────────────────────────────────────────────────────────
# ONE emitter for the life of the engine, and plain worker threads.
#
# It was a QRunnable per pass, each carrying its own parentless QObject to
# signal through. That crashes. Reproduced in isolation on 2026-09-17 with
# nothing else in the program — forty lines, a QRunnable made on the GUI thread
# with `self.signals = _Signals()`, handed to a QThreadPool, emitting from the
# worker: an access violation within a couple of thousand passes, which is an
# hour or two of running. The reason is ownership. QThreadPool deletes the C++
# runnable when run() returns, PySide6 then invalidates the Python wrapper, and
# the parentless signals object hanging off it goes with it — sometimes while
# its own queued emission is still in the GUI thread's event queue. Holding a
# Python reference to the runnable does not help: it turns the crash into
# "RuntimeError: Signal source has been deleted", which is the same bug wearing
# a coat.
#
# So: no QRunnable, no QThreadPool, no per-pass QObject. One `_PassSignals`
# parented to the engine, which therefore lives exactly as long as the engine
# and of which exactly one is ever made — that also settles the other half of
# the problem, that an object created twice a second and never collected is a
# few kilobytes a time and half a gigabyte a week.
#
# Verified with the same isolation harness: 1248 passes at twenty-five times the
# real rate, about ten hours of running, every result delivered, no crash.


class _PassSignals(QObject):
    values_done = Signal(int, object)      # generation, {item id: Reading}
    areas_done = Signal(int, object)       # generation, {item id: (diff, err, size)}


def _read_values(reader, items, deadline_s):
    """One archiver pass. Runs on a worker thread; raises nothing."""
    try:
        return reader.read_many(items, deadline_s=deadline_s,
                                readable_error=C.readable_pv_error)
    except Exception as exc:          # a pass must never take the program down
        return {int(it["id"]): A.Reading(error=f"read failed: {exc}")
                for it in items}


def _compare_areas(jobs):
    """One grab-and-compare pass. Runs on a worker thread; raises nothing.

    `jobs` is [(item id, region, reference image)]; the answer is
    {item id: (difference, error, size now)}.

    Grabbing on a worker thread was measured on 2026-09-16: a grab from a worker
    returns exactly the same rectangle as one from the GUI thread and leaves the
    GUI thread's own view of the screens untouched. Doing it here keeps the
    comparison — real work on a big rectangle — out of the event loop.

    ONE photograph answers every area, however many there are. It used to be one
    per area, and each of those photographs the whole desktop before it crops
    (269 ms, 30 MB, measured), so four areas could not be done in the half
    second they were given. See `S.grab_regions`.
    """
    out = {}
    try:
        shots = S.grab_regions([region for _iid, region, _ref in jobs])
    except Exception as exc:            # a pass must never take the program down
        return {iid: (None, f"could not be compared: {exc}", None)
                for iid, _region, _ref in jobs}
    for (iid, _region, reference), (img, err) in zip(jobs, shots):
        try:
            if err is not None:
                out[iid] = (None, err, None)
                continue
            diff = S.picture_diff(img, reference) if reference is not None else None
            out[iid] = (diff, None, img.size)
        except Exception as exc:
            out[iid] = (None, f"could not be compared: {exc}", None)
    return out


# ── the engine ───────────────────────────────────────────────────────────────

class WatchEngine(QObject):
    """Holds the verdicts. Everything on screen is a rendering of this."""

    # Something's verdict changed — repaint.
    changed = Signal()
    # An item raised the alarm: (item id, the operator's own sentence).
    fired = Signal(int, str)
    # Watching started or stopped, for the circle and the buttons.
    watching_changed = Signal(bool)
    # A sentence for the log, with an optional longer explanation.
    logged = Signal(str, object)

    def __init__(self, get_items, parent=None):
        super().__init__(parent)
        self._get_items = get_items
        self._states = {}
        self._watching = False
        self._closing = False

        # One reader for the life of the program: one thread pool, and one
        # memory of which look-back window answered for which channel.
        self._reader = A.PassReader(workers=6)

        # One emitter for the life of the engine, parented to it. See the
        # note above the pass functions for why this is not one per pass.
        self._sig = _PassSignals(self)
        self._sig.values_done.connect(self._value_done)
        self._sig.areas_done.connect(self._area_done)

        # ONE THREAD EACH, in two separate pools — not two threads in one.
        #
        # A shared pool of two is how both clocks die together. A pass cannot be
        # cancelled: the watchdog clears the flag and moves the generation on,
        # but the thread is still in there. Two genuinely hung passes — a locked
        # session makes `ImageGrab` block, a stalled socket outlives its timeout
        # — occupied both threads for good, and every later pass from EITHER
        # clock then queued behind them for ever, one every half second, each
        # holding a snapshot of the items and PIL references. The log went on
        # saying "written off" while nothing was ever read again.
        #
        # Separate pools mean a wedged picture pass can never cost the archiver
        # its turn, which was the stated intent all along.
        self._value_pool = ThreadPoolExecutor(max_workers=1,
                                              thread_name_prefix="ann-values")
        self._area_pool = ThreadPoolExecutor(max_workers=1,
                                             thread_name_prefix="ann-areas")

        self._value_gen = 0
        self._value_inflight = False
        self._value_started = 0.0
        self._value_zombies = 0

        self._area_gen = 0
        self._area_inflight = False
        self._area_started = 0.0
        self._area_zombies = 0

        self._ref_cache = {}            # item id -> (reference text, PIL image)

        # PreciseTimer, not the default. A plain QTimer on Windows is a coarse
        # timer: asked for 500 ms it fires at whatever the 15 ms tick gives it,
        # and a 33 ms one measured 21 Hz instead of 30.
        self._value_timer = QTimer(self)
        self._value_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._value_timer.timeout.connect(self._value_tick)
        self._area_timer = QTimer(self)
        self._area_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._area_timer.timeout.connect(self._area_tick)
        self._apply_interval()
        self._value_timer.start()
        self._area_timer.start()

    # ── what the tabs ask ───────────────────────────────────────────────────
    def state_of(self, item_id):
        st = self._states.get(int(item_id))
        if st is None:
            st = self._states[int(item_id)] = ItemState()
        return st

    @property
    def watching(self):
        return self._watching

    def items(self):
        return self._get_items() or []

    def item_by_id(self, item_id):
        for it in self.items():
            if int(it.get("id", -1)) == int(item_id):
                return it
        return None

    def unread_for_s(self):
        """The longest any watched row has gone without a good read, in seconds.

        None when everything is being read, or when there is nothing to read.
        This is what puts the exclamation mark beside the circle (see
        `C.NO_DATA_S`) and it is the ONLY thing said on screen about readings
        that are not arriving.

        Only rows that are switched on and CAN be read count. A value with no
        channel typed in and an area with no rectangle drawn are things nobody
        has finished setting up, not readings that have stopped coming — they
        would pin the mark on for ever and it would mean nothing.
        """
        now = time.monotonic()
        worst = None
        for it in self.items():
            if not it.get("on"):
                continue
            if it.get("kind") == "area":
                if not it.get("region") or not it.get("reference"):
                    continue
            elif not (it.get("pv") or "").strip():
                continue
            st = self.state_of(int(it["id"]))
            last = st.answered_at if st.answered_at is not None else st.since
            age = now - last
            if worst is None or age > worst:
                worst = age
        return worst

    def no_data(self):
        """True when the readings have been failing long enough to say so."""
        age = self.unread_for_s()
        return age is not None and age >= C.NO_DATA_S

    def reasons_it_cannot_fire(self):
        """Every switched-on item that could never raise anything, and why.

        Said out loud when watching starts, rather than letting a ticked row sit
        there doing nothing.
        """
        out = []
        for it in self.items():
            if not it.get("on") or it.get("fires") != "alarm":
                continue
            name = it.get("name") or "(unnamed)"
            if it.get("kind") == "area":
                if not it.get("region"):
                    out.append(f'"{name}" has no area drawn, so it cannot fire')
                elif not it.get("reference"):
                    out.append(f'"{name}" has no reference picture, so it cannot fire')
            else:
                if all(it.get(k) is None for k in ("lo_lo", "lo", "hi", "hi_hi")):
                    out.append(f'"{name}" has no limit set, so it cannot fire')
                elif it.get("lo_lo") is None and it.get("hi_hi") is None:
                    out.append(f'"{name}" has only a warning limit, so it can '
                               f'colour itself but not raise the alarm')
        return out

    # ── starting and stopping ───────────────────────────────────────────────
    def start(self):
        """Arm the alarm. Reading was already happening."""
        if self._watching:
            return
        for st in self._states.values():
            st.fired = False
        self._watching = True
        self._apply_interval()
        for line in self.reasons_it_cannot_fire():
            self.logged.emit(line, None)
        self.watching_changed.emit(True)
        self.changed.emit()

    def stop(self):
        if not self._watching:
            return
        self._watching = False
        self._apply_interval()
        self.watching_changed.emit(False)
        self.changed.emit()

    def reset(self):
        """Forget that anything fired, and arm again."""
        for st in self._states.values():
            st.fired = False
            st.latched = False
        self.changed.emit()

    def _apply_interval(self):
        ms = C.POLL_MS_WATCHING if self._watching else C.POLL_MS_IDLE
        self._value_timer.setInterval(ms)
        self._area_timer.setInterval(ms)

    # ── the value clock ─────────────────────────────────────────────────────
    def _value_tick(self):
        interval_s = self._value_timer.interval() / 1000.0
        if self._value_inflight:
            self._check_wedge("values", interval_s)
            return
        items = [it for it in self.items()
                 if it.get("kind") == "value" and (it.get("pv") or "").strip()]
        self._refresh_staleness(items)
        if not items:
            return
        self._value_gen += 1
        self._value_inflight = True
        self._value_started = time.monotonic()
        # A pass may take at most most of one interval's worth of grace beyond
        # a single request's timeout; past that the values are simply late, and
        # saying so beats waiting.
        deadline = max(2.0, min(8.0, interval_s * 4))
        # A copy of the list, so an edit on the GUI thread cannot change what
        # the worker is reading half way through.
        snapshot = [dict(it) for it in items]
        gen = self._value_gen
        self._value_pool.submit(self._value_work, gen, snapshot, deadline)

    def _value_work(self, generation, items, deadline_s):
        """Runs on a worker thread. Always emits, whatever happens.

        Always, because the single-flight guard is cleared by the result slot:
        a pass that returned nothing at all would leave the guard set until the
        watchdog noticed, and the watchdog is for a read that really has hung,
        not for a bug in here.
        """
        try:
            out = _read_values(self._reader, items, deadline_s)
        except Exception as exc:
            out = {int(it["id"]): A.Reading(error=f"read failed: {exc}")
                   for it in items}
        if self._closing:
            return                      # see `shutdown`
        self._sig.values_done.emit(generation, out)

    def _area_work(self, generation, jobs):
        """Runs on a worker thread. Always emits, whatever happens."""
        try:
            out = _compare_areas(jobs)
        except Exception as exc:
            out = {iid: (None, f"could not be compared: {exc}", None)
                   for iid, _r, _ref in jobs}
        if self._closing:
            return                      # see `shutdown`
        self._sig.areas_done.emit(generation, out)

    def _value_done(self, generation, readings):
        self._value_inflight = False
        if generation != self._value_gen:
            return                      # a written-off pass answering late
        now = time.monotonic()
        for iid, reading in readings.items():
            it = self.item_by_id(iid)
            if it is None:
                continue                # edited or deleted while in flight
            st = self.state_of(iid)
            if reading.ok and reading.value is not None:
                st.read_at = now
            if not reading.error:
                st.answered_at = now
            st.value = reading.value
            st.hint = reading.hint
            st.state, st.text = C.judge_value(
                it, reading.value, error=reading.error,
                samples=reading.samples, age_s=st.age_s)
            if reading.error and not st.latched:
                self._say_error(st, reading.error, reading.hint)
            elif not reading.error:
                st.err_said = None      # cleared, so a return of it is news
        self._settle()

    def _say_error(self, st, error, hint):
        """A failing channel says so ONCE, not twice a second for ever.

        `latched` only ever covers items that have fired, and a failed read is
        "unknown", which never fires — so a dead channel (this facility has had
        one: two names for the same thing, taking turns) emitted two log lines a
        second for as long as the program was open. Each one is an
        `appendPlainText` plus `ensureCursorVisible()` on the GUI thread, and it
        drowned everything else in the log.

        Re-announced when the WORDS change, or after a cool-off, so "still
        broken" is still visible without being the only thing visible.
        """
        now = time.monotonic()
        said = getattr(st, "err_said", None)
        if said is not None and said[0] == error and now - said[1] < _ERROR_AGAIN_S:
            return
        st.err_said = (error, now)
        self.logged.emit(error, hint)

    # ── the picture clock ───────────────────────────────────────────────────
    def _area_tick(self):
        interval_s = self._area_timer.interval() / 1000.0
        if self._area_inflight:
            self._check_wedge("pictures", interval_s)
            return
        jobs = []
        areas = [it for it in self.items() if it.get("kind") == "area"]
        for it in areas:
            iid = int(it["id"])
            st = self.state_of(iid)
            if not it.get("region") or not it.get("reference"):
                st.state, st.text = C.judge_area(it, None)
                continue
            # A copy of the rectangle, for the same reason the values are
            # snapshotted: an edit on the GUI thread must not change what the
            # worker is photographing half way through.
            jobs.append((iid, list(it["region"]), self._reference_for(it)))
        self._refresh_staleness(areas)
        if not jobs:
            return
        self._area_gen += 1
        self._area_inflight = True
        self._area_started = time.monotonic()
        gen = self._area_gen
        self._area_pool.submit(self._area_work, gen, jobs)

    def _area_done(self, generation, results):
        self._area_inflight = False
        if generation != self._area_gen:
            return
        now = time.monotonic()
        for iid, (diff, err, size_now) in results.items():
            it = self.item_by_id(iid)
            if it is None:
                continue
            st = self.state_of(iid)
            ref = self._ref_cache.get(iid, (None, None))[1]
            size_ref = ref.size if ref is not None else None
            if err is None:
                st.read_at = now
                st.answered_at = now
            st.diff = diff
            st.size_now = size_now
            st.state, st.text = C.judge_area(
                it, diff, error=err, size_now=size_now, size_ref=size_ref,
                age_s=st.age_s)
            if err and not st.latched:
                self._say_error(st, f'{it.get("name") or "an area"} — {err}',
                                None)
            elif not err:
                st.err_said = None
        self._settle()

    def _reference_for(self, item):
        """The decoded reference picture, decoded once per stored picture."""
        iid = int(item["id"])
        text = item.get("reference")
        cached = self._ref_cache.get(iid)
        if cached is not None and cached[0] == text:
            return cached[1]
        img = C.decode_reference(text)
        self._ref_cache[iid] = (text, img)
        return img

    def forget_reference(self, item_id):
        self._ref_cache.pop(int(item_id), None)

    # ── shared ──────────────────────────────────────────────────────────────
    def _check_wedge(self, what, interval_s):
        """Write off a pass that never came back.

        The pass cannot be cancelled, so it is abandoned: the flag is cleared,
        the generation moves on, and whatever it eventually returns is dropped
        by `_value_done` / `_area_done`. Said out loud and counted, because a
        program quietly showing the last good numbers for ever is the failure
        that looks most like success.
        """
        started = self._value_started if what == "values" else self._area_started
        limit = max(5 * interval_s, 3 * A.DEFAULT_TIMEOUT + 15.0)
        waited = time.monotonic() - started
        if waited < limit:
            return
        if what == "values":
            self._value_inflight = False
            self._value_gen += 1
            self._value_zombies += 1
            n = self._value_zombies
        else:
            self._area_inflight = False
            self._area_gen += 1
            self._area_zombies += 1
            n = self._area_zombies
        self.logged.emit(
            f"A {what} read has not come back after {waited:.0f} s — writing it "
            f"off and starting again (this has happened {n} time"
            f"{'s' if n != 1 else ''} since the program started).", None)

    def _refresh_staleness(self, items):
        """Re-judge on the heartbeat, so "not refreshed" appears on its own.

        Without this, a program whose reads have all stopped would sit showing
        the last good values as though they were current.
        """
        for it in items:
            iid = int(it.get("id", -1))
            st = self._states.get(iid)
            if st is None or st.read_at is None:
                continue
            if st.state in (C.STATE_OK, C.STATE_WARN, C.STATE_TRIP) \
                    and st.age_s is not None and st.age_s > C.STALE_AFTER_S:
                if it.get("kind") == "area":
                    st.state, st.text = C.judge_area(it, st.diff, age_s=st.age_s)
                else:
                    st.state, st.text = C.judge_value(
                        it, st.value, samples=1, age_s=st.age_s)

    def _settle(self):
        """Decide what fires, honouring the groups, then tell everyone."""
        items = self.items()
        states = {int(it["id"]): self.state_of(int(it["id"])).state
                  for it in items}
        firing = C.firing_ids(items, states)

        for it in items:
            iid = int(it["id"])
            st = self.state_of(iid)
            if iid not in firing:
                # Back to normal: ready to be announced again if it goes wrong
                # a second time.
                st.latched = False
                continue
            if st.latched:
                continue
            st.latched = True
            sentence = C.fire_sentence(it, st.text)
            self.logged.emit(f'{it.get("name") or "Something"} — {sentence} '
                             f'({st.text})', st.hint)

        if self._watching:
            for iid in sorted(firing):
                it = self.item_by_id(iid)
                if it is None or it.get("fires") != "alarm":
                    continue
                st = self.state_of(iid)
                if st.fired:
                    continue
                st.fired = True
                self.fired.emit(iid, C.fire_sentence(it, st.text))
                # One alarm at a time. Whoever is listening stops the watch, so
                # nothing re-alarms twice a second; Reset arms it again.
                break

        self.changed.emit()

    # ── housekeeping ────────────────────────────────────────────────────────
    def drop_missing(self):
        """Forget the state of items that no longer exist."""
        live = {int(it["id"]) for it in self.items()}
        for iid in [i for i in self._states if i not in live]:
            self._states.pop(iid, None)
            self._ref_cache.pop(iid, None)

    def stats_line(self):
        return (A.stats_line() +
                f" wedges={self._value_zombies + self._area_zombies}")

    def shutdown(self):
        # Set FIRST, and checked by both workers right before they emit. A read
        # in flight cannot be cancelled, so it will finish after this returns —
        # and a worker thread emitting into a QObject that is being destroyed is
        # the same use-after-free that used to kill this program. It must go
        # quietly instead.
        self._closing = True
        self._value_timer.stop()
        self._area_timer.stop()
        self._reader.close()
        try:
            self._sig.values_done.disconnect()
            self._sig.areas_done.disconnect()
        except (RuntimeError, TypeError):
            pass                        # already gone, or never connected
        self._value_pool.shutdown(wait=False)
        self._area_pool.shutdown(wait=False)
