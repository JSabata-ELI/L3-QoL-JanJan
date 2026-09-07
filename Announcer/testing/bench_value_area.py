"""A value condition with a screen area attached: prove that the picture half
raises the alarm on its own, that an unchanged picture does not, and that the
value half still works with an area present.

The screen grab is stubbed. Watching real pixels makes the result depend on
whether the desktop happened to repaint a window that may be locked, covered or
on a monitor this session cannot see — which says nothing about the program.
What is under test is the wiring: that _poll compares the attached area at all,
against the right reference and the right sensitivity, and reports which half
failed. The value half is left real and does talk to the archiver.

Run:  set PYTHONIOENCODING=utf-8 && python bench_value_area.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PIL import Image  # noqa: E402
import a               # noqa: E402

WATCH_MS = 5000
FAILED = []

SIZE = (120, 60)
QUIET = Image.new("RGB", SIZE, (250, 250, 250))
NUDGED = Image.new("RGB", SIZE, (247, 247, 247))     # avg diff 3, over a 2.0 limit
LOUD = Image.new("RGB", SIZE, (20, 20, 20))
WRONG_SIZE = Image.new("RGB", (90, 60), (250, 250, 250))


def check(name, got, want):
    if got != want:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")
        print(f"FAIL  {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok    {name}")


class Bench:
    def __init__(self):
        self.app = a.ScreenTracker()
        self.app._presets_path = Path(tempfile.gettempdir()) / "announcer_bench_presets.json"
        self.app.geometry("820x620+40+40")
        self.app.sound_enabled.set(False)
        self.app.flash_duration.set(0.5)

        # What the watched area "shows" at any moment.
        self.shown = QUIET
        self.app._grab_rect = lambda rect: self.shown
        self.steps = []

    def run(self):
        self.app.after(300, self._next)
        self.app.mainloop()
        return 1 if FAILED else 0

    def _next(self):
        if not self.steps:
            print()
            print(f"{len(FAILED)} FAILED" if FAILED else "all passed")
            self.app.destroy()
            return
        self.steps.pop(0)()

    def condition(self, trip=None, threshold=2.0):
        """A value condition with an area attached; trip=None = picture only."""
        return {"kind": "pv", "name": "Area bench", "enabled": True,
                "message": "the picture stopped matching",
                "pv": a.COND_DEFAULT_PV, "warn": None, "trip": trip, "unit": "",
                "gate_monitor": 0, "gate_region": [0, 0, SIZE[0], SIZE[1]],
                "gate_threshold": threshold,
                "gate_reference": self.app._encode_reference(QUIET),
                "_gate_img": QUIET,
                "_uid": self.app._next_cond_uid()}

    def case(self, name, expect_alarm, cond, becomes=None, then=None,
             watch_ms=WATCH_MS):
        def start():
            print(f"\n-- {name}")
            self.shown = QUIET
            self.app._conditions = [cond()]
            self.app._refresh_cond_tree()
            self.app.changed = False
            self.app._start_tracking()
            if becomes is not None:
                self.app.after(1200, lambda: setattr(self, "shown", becomes))
            self.app.after(watch_ms, finish)

        def finish():
            fired = self.app.changed
            status = self.app.status_var.get()
            if self.app.tracking:
                self.app._stop_tracking()
            if self.app.changed:
                self.app._reset()
            check(name, fired, expect_alarm)
            print(f"      status: {status}")
            if then:
                then(status)
            self.app.after(300, self._next)

        self.steps.append(start)


def main():
    b = Bench()

    b.case("an unchanged picture does not fire", False, lambda: b.condition())

    b.case("a change under the sensitivity does not fire", False,
           lambda: b.condition(threshold=5.0), becomes=NUDGED)

    def says_picture(status):
        check("the alarm says it was the picture",
              "screen area changed" in status, True)

    b.case("a change over the sensitivity fires", True,
           lambda: b.condition(threshold=2.0), becomes=NUDGED, then=says_picture)

    b.case("a big change fires", True, lambda: b.condition(), becomes=LOUD)

    def says_size(status):
        check("and it says both sizes",
              ("90x60" in status and "120x60" in status), True)

    b.case("an area that changed size fires rather than passing", True,
           lambda: b.condition(), becomes=WRONG_SIZE, then=says_size)

    # The value half must still work when an area is attached: a trip level of
    # zero on an energy fires on the first shot. This one is real, so it needs
    # time for the archiver round trip.
    def says_value(status):
        check("and it says it was the value, not the picture",
              "is over" in status, True)

    b.case("the value half still fires with an area attached", True,
           lambda: b.condition(trip=0.0), then=says_value, watch_ms=12000)

    return b.run()


if __name__ == "__main__":
    sys.exit(main())
