"""End to end against the real archiver: read the two hall values, then prove
that a hall condition trips when the machine disagrees and stays quiet when it
does not.

Runs inside a real mainloop — the program hands results back from its worker
thread with after(), which only arrives when the loop is actually running.
Writes nothing to the real presets.json: the settings path is redirected to a
temporary file before anything can save.

Run:  set PYTHONIOENCODING=utf-8 && python bench_hall_live.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import a  # noqa: E402

WATCH_MS = 20000        # how long each case is left running
FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"FAIL  {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok    {name}")


class Bench:
    def __init__(self):
        self.app = a.ScreenTracker()
        # Nothing this bench does may reach the real settings file.
        self.app._presets_path = Path(tempfile.gettempdir()) / "announcer_bench_presets.json"
        self.app.geometry("820x620+80+60")
        self.app.sound_enabled.set(False)
        self.app.flash_duration.set(0.5)
        self.steps = []
        self.now_pss = None

    # -- plumbing ---------------------------------------------------------
    def run(self):
        self.app.after(200, self._next)
        self.app.mainloop()
        return 1 if FAILED else 0

    def _next(self):
        if not self.steps:
            print()
            print(f"{len(FAILED)} FAILED" if FAILED else "all passed")
            self.app.destroy()
            return
        self.steps.pop(0)()

    def case(self, name, expect_alarm, cond, then=None):
        """Watch one condition for a while, then say whether it fired.

        Finishes the moment the alarm goes off rather than sitting in the
        alarmed state for the rest of the window. That is what a person does,
        and leaving a borderless topmost window up for twenty seconds while
        other things run on the desktop got the bench closed under itself.
        """
        def start():
            print(f"\n-- {name}")
            self.app._conditions = [dict(cond, _uid=self.app._next_cond_uid())]
            self.app._refresh_cond_tree()
            self.app.changed = False
            self.app._start_tracking()
            watch(WATCH_MS)

        def watch(left_ms):
            if self.app.changed or left_ms <= 0:
                finish()
                return
            self.app.after(300, lambda: watch(left_ms - 300))

        def finish():
            fired = self.app.changed
            status = self.app.status_var.get()
            if self.app.tracking:
                self.app._stop_tracking()
            if self.app.changed:
                self.app._reset()
            check(name, fired, expect_alarm)
            print(f"      status:  {status}")
            print(f"      readout: {self.app._hall_readout.get()}")
            if then:
                then(status)
            self.app.after(200, self._next)

        self.steps.append(start)


def main():
    b = Bench()
    app = b.app

    def read_first():
        fate, pss = app._read_hall_pvs()
        print("readout:", app._hall_readout_text(fate, pss))
        if pss is None:
            print("PSS state could not be read — cannot run the live cases.")
            FAILED.append("PSS state unreadable")
            b.steps = []
        else:
            b.now_pss = pss[0]
            build_cases(b)
        app.after(0, b._next)

    b.steps.append(read_first)
    b.run_result = b.run()
    return 1 if FAILED else 0


def build_cases(b):
    now = b.now_pss

    # 1. What the machine says is what was set: no alarm.
    b.case("matching PSS state does not fire", False,
           {"kind": "hall", "name": "Hall bench OK", "enabled": True,
            "message": "", "hall": None, "pss": now, "moving_grace_s": 60})

    # 2. The opposite of what the machine says: alarm, naming both states.
    def names_both(status):
        check("the alarm says what the machine actually is",
              a._pss_name(now) in status, True)
        check("and what was expected", a._pss_name(1 - now) in status, True)

    b.case("the wrong PSS state fires", True,
           {"kind": "hall", "name": "Hall bench TRIP", "enabled": True,
            "message": "", "hall": None, "pss": 1 - now, "moving_grace_s": 60},
           then=names_both)

    # 3. Beam fate is not archived yet: it must say so, and never trip.
    def says_missing(_status):
        check("the beam fate is not silently missing",
              "cannot be read" in b.app._hall_readout.get(), True)

    b.case("an unreadable beam fate never fires", False,
           {"kind": "hall", "name": "Hall bench FATE", "enabled": True,
            "message": "", "hall": 3, "pss": None, "moving_grace_s": 60},
           then=says_missing)


if __name__ == "__main__":
    sys.exit(main())
