"""Laser settings — the clock that keeps the numbers coming.

One pass a second: every hall's shooting channel and every watched channel, all
read side by side, and the answers handed back to the window in one go.

WHY IT IS BUILT THE WAY IT IS
-----------------------------
This is `Announcer/ann_watch.py`'s dispatch, kept to the letter, because the
shape of it was paid for:

  * NO QRunnable and NO QThreadPool. A runnable carrying its own parentless
    signals object crashes with an access violation within a couple of thousand
    passes — reproduced in isolation on 2026-09-17. QThreadPool deletes the C++
    runnable when run() returns and the signals object hanging off it goes with
    it, sometimes while its own queued emission is still in the GUI thread's
    queue. Holding a Python reference only turns it into "Signal source has been
    deleted", which is the same bug wearing a coat. So: ONE `_PassSignals`
    parented to the engine, made exactly once. That also settles the other half
    — an object made every second and never collected is half a gigabyte a week.

  * SINGLE FLIGHT with a generation number. A pass that is still out means the
    next tick does nothing; a late answer from an older generation is dropped
    rather than painted over a newer one.

  * A WATCHDOG on the single-flight flag. A read that never comes back cannot be
    cancelled, so after a while it is written off, said out loud and counted. A
    program quietly showing the last good numbers for ever is the failure that
    looks most like success.

  * A PRECISE timer. A default QTimer asked for a short interval fires coarsely
    on Windows.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, QTimer, Signal

import ls_core as C
import ls_cpva as A
import ls_dm
import ls_screen


class _PassSignals(QObject):
    """Exactly one of these is ever made, and it is parented to the engine."""
    done = Signal(int, object)          # generation, {channel: Reading}


def _read(reader, channels, deadline_s):
    """One archiver pass. Runs on a worker thread; raises nothing."""
    try:
        return reader.read_many(channels, deadline_s=deadline_s,
                                readable_error=C.readable_pv_error)
    except Exception as exc:            # a pass must never take the program down
        return {name: A.Reading(error=f"read failed: {exc}") for name in channels}


class PollEngine(QObject):
    """Reads every channel the window cares about, once a second, for ever.

    It holds no opinion about halls or limits — it hands back what each channel
    is holding, and `ls_core` decides what that means. The one judgement it does
    make is about its own health: when a channel was last read successfully, so
    a row can stop claiming a value nobody has refreshed.
    """

    updated = Signal()                  # a pass came back; repaint everything
    logged = Signal(str, object)        # message, hint

    def __init__(self, get_channels, parent=None, want_screen=None,
                 want_dm=None):
        super().__init__(parent)
        self._get_channels = get_channels
        self._readings = {}             # channel -> Reading
        self._last_ok = {}              # channel -> time.time() of last value
        self._failing = set()           # channels whose last read failed
        self._closing = False

        # The L3BT MSS window, looked at once a pass while `want_screen` says
        # some valve cannot answer from its channels (see `ls_screen`). Its own
        # thread and its own single-flight flag: drawing another program's
        # window waits on that program, and a hung MSS must not hold up the
        # archiver reads. A look that never comes back simply goes stale.
        self._want_screen = want_screen
        self._screen = None             # the last ls_screen.ScreenRead
        self._screen_busy = False
        self._screen_pool = ThreadPoolExecutor(max_workers=1,
                                               thread_name_prefix="screen")

        # The deformable mirror (see `ls_dm`). Every 5 s, on its own thread
        # with its own single-flight flag, for the same reason as the screen:
        # the share can hang for 48 s and the Remote Desktop window is another
        # program. `want_dm()` says (write, title): `write` on the one PC that
        # photographs the window and writes the state file. Every PC reads the
        # file back, so every PC — that one included — shows what everybody
        # else sees. Edits to the saved set-ups are queued here and done on the
        # same thread, never on the GUI's.
        self._want_dm = want_dm
        self._dm = None                 # the last DmView
        self._dm_busy = False
        self._dm_next = 0.0
        self._dm_hint = None
        self._dm_edits = []             # callables: setups -> (setups, note)
        # The picture of the window goes to the share by itself the first time
        # the grid is not found (and again after it has been found since), and
        # whenever the DM writer tab asks — never every 5 s.
        self._dm_picture_asked = False
        self._dm_last_status = None
        self._dm_picture_note = None
        self._dm_share = ls_dm.Share()
        self._dm_pool = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix="dm")

        self._reader = A.PassReader(workers=C.POLL_WORKERS)
        self._sig = _PassSignals(self)
        self._sig.done.connect(self._pass_done)

        # One worker to dispatch passes. The reads themselves fan out inside
        # `PassReader`; this thread only waits for them, so one is enough and
        # two would let a slow pass overtake a fast one.
        self._pool = ThreadPoolExecutor(max_workers=1,
                                        thread_name_prefix="pass")
        self._gen = 0
        self._inflight = False
        self._started = 0.0
        self._zombies = 0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(C.POLL_MS)
        self._timer.timeout.connect(self._tick)

    # ── what the window asks ───────────────────────────────────────────────
    def start(self):
        if not self._timer.isActive():
            self._timer.start()
            QTimer.singleShot(0, self._tick)    # do not wait a second to begin

    def reading(self, channel):
        """What this channel is holding, or None if it has never been read."""
        return self._readings.get((channel or "").strip())

    def readings(self):
        return self._readings

    def last_ok(self):
        """{channel: when we last got a real value out of it}."""
        return self._last_ok

    def last_ok_for(self, channel):
        return self._last_ok.get((channel or "").strip())

    def screen(self):
        """The last look at the L3BT MSS window, or None when none is wanted."""
        return self._screen

    def dm(self):
        """The last `DmView` of the deformable mirror, or None before one."""
        return self._dm

    def dm_edit(self, edit):
        """Change the saved set-ups on the dm thread, and look again at once.

        `edit(setups)` returns (new setups or None, a sentence or None). The
        sentence comes back as `DmView.note`.
        """
        self._dm_edits.append(edit)
        self._dm_next = 0.0

    def dm_save_picture(self):
        """Save the next picture of the Remote Desktop window to the share."""
        self._dm_picture_asked = True
        self._dm_next = 0.0

    def forget(self, channel=None):
        """A channel name was edited: drop what we knew about the old one."""
        self._reader.forget(channel)
        if channel is None:
            self._readings.clear()
            self._last_ok.clear()
        else:
            self._readings.pop(channel, None)
            self._last_ok.pop(channel, None)

    def stats_line(self):
        return A.stats_line() + f" wedges={self._zombies}"

    # ── the clock ──────────────────────────────────────────────────────────
    def _tick(self):
        self._look_at_screen()
        self._look_at_dm()
        interval_s = max(0.2, self._timer.interval() / 1000.0)
        if self._inflight:
            self._check_wedge(interval_s)
            return
        channels = [c for c in (self._get_channels() or []) if (c or "").strip()]
        if not channels:
            # Nothing set up yet. Still tell the window, so a row that has gone
            # stale says so instead of showing yesterday's number as current.
            self.updated.emit()
            return
        self._gen += 1
        self._inflight = True
        self._started = time.monotonic()
        deadline = max(2.0, min(8.0, interval_s * 4))
        self._pool.submit(self._work, self._gen, list(channels), deadline)

    def _work(self, generation, channels, deadline_s):
        """Runs on a worker thread. Always emits, whatever happens.

        Always, because the single-flight guard is cleared by the result slot: a
        pass that returned nothing at all would leave the guard set until the
        watchdog noticed, and the watchdog is for a read that really has hung,
        not for a bug in here.
        """
        try:
            out = _read(self._reader, channels, deadline_s)
        except Exception as exc:
            out = {name: A.Reading(error=f"read failed: {exc}")
                   for name in channels}
        if self._closing:
            return                      # see `shutdown`
        self._sig.done.emit(generation, out)

    def _pass_done(self, generation, out):
        if generation != self._gen:
            return                      # a zombie pass finally came back
        self._inflight = False
        now = time.time()
        for name, reading in out.items():
            prev = self._readings.get(name)
            seen = self._last_ok.get(name)
            if (reading.error and prev is not None and prev.has_value
                    and seen is not None and now - seen <= C.STALE_AFTER_S):
                # One read that failed or was not answered in time (from the
                # office the archiver often takes longer than a pass allows)
                # does not throw away a good value a few seconds old: it is
                # kept until it is `STALE_AFTER_S` old, and then the row says
                # "not read for N s" as before (13.10.2026 — GDD, TOD and SBW4
                # flickered to a dash in the office).
                if name not in self._failing:
                    self._failing.add(name)
                    self.logged.emit(f"{name}: a read failed ({reading.error})"
                                     f" — the last value is kept for up to "
                                     f"{int(C.STALE_AFTER_S)} s", None)
                continue
            self._readings[name] = reading
            if reading.has_value:
                self._last_ok[name] = now
                self._failing.discard(name)
        self.updated.emit()

    def _look_at_screen(self):
        if self._screen_busy or self._closing:
            return
        try:
            wanted = self._want_screen is not None and self._want_screen()
        except Exception:
            wanted = False
        if not wanted:
            self._screen = None
            return
        self._screen_busy = True
        self._screen_pool.submit(self._screen_work)

    def _screen_work(self):
        """Runs on the screen thread. `read_window` raises nothing."""
        try:
            self._screen = ls_screen.read_window()
        finally:
            self._screen_busy = False

    def _look_at_dm(self):
        if self._dm_busy or self._closing or self._want_dm is None:
            return
        now = time.monotonic()
        if now < self._dm_next:
            return
        try:
            write, title, every = self._want_dm()
        except Exception:
            write, title, every = False, ls_dm.DEFAULT_TITLE, ls_dm.PERIOD_S
        if not write:
            # A reading PC follows the writing PC's pace, which is in the
            # file it reads: no point asking the share more often than that.
            state = self._dm.state if self._dm is not None else None
            every = C.dm_every((state or {}).get("every_s"))
        self._dm_next = now + every
        edits, self._dm_edits = self._dm_edits, []
        self._dm_busy = True
        self._dm_pool.submit(self._dm_work, bool(write), title, edits, every)

    def _dm_work(self, write, title, edits, every=ls_dm.PERIOD_S):
        """Runs on the dm thread. Raises nothing; a failure is a sentence."""
        try:
            local = None
            save = False
            if write:
                local = ls_dm.read_window(title, self._dm_hint)
                if local.bar is not None:
                    self._dm_hint = local.bar
                failed = local.status == ls_dm.STATUS_NO_GRID
                taken = (local.picture is not None
                         or local.screen_picture is not None)
                save = taken and (
                    self._dm_picture_asked
                    or (failed and self._dm_last_status != local.status))
                self._dm_last_status = local.status
            if save:
                self._dm_picture_asked = False
            view = ls_dm.exchange(self._dm_share, local, edits, save, every)
            if view.picture_note:
                self._dm_picture_note = view.picture_note
            self._dm = view._replace(picture_note=self._dm_picture_note)
        except Exception as exc:
            self._dm = ls_dm.DmView(None, {}, None,
                                    f"The mirror's state could not be "
                                    f"handled: {exc}", None, time.time())
        finally:
            self._dm_busy = False

    def _check_wedge(self, interval_s):
        """Write off a pass that never came back.

        The pass cannot be cancelled, so it is abandoned: the flag is cleared,
        the generation moves on, and whatever it eventually returns is dropped
        by `_pass_done`.
        """
        limit = max(5 * interval_s, 3 * A.DEFAULT_TIMEOUT + 15.0)
        waited = time.monotonic() - self._started
        if waited < limit:
            return
        self._inflight = False
        self._gen += 1
        self._zombies += 1
        self.logged.emit(
            f"A read has not come back after {waited:.0f} s — writing it off "
            f"and starting again (this has happened {self._zombies} time"
            f"{'s' if self._zombies != 1 else ''} since the program started).",
            None)
        self.updated.emit()

    # ── shutting down ──────────────────────────────────────────────────────
    def shutdown(self):
        # Set FIRST, and checked by the worker right before it emits. A read in
        # flight cannot be cancelled, so it will finish after this returns — and
        # a worker thread emitting into a QObject that is being destroyed is a
        # use-after-free. It must go quietly instead.
        self._closing = True
        self._timer.stop()
        self._reader.close()
        try:
            self._sig.done.disconnect()
        except (RuntimeError, TypeError):
            pass                        # already gone, or never connected
        self._pool.shutdown(wait=False)
        self._screen_pool.shutdown(wait=False)
        self._dm_pool.shutdown(wait=False)
