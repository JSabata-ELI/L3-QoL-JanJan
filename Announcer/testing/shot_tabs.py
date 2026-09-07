"""Open the Announcer and photograph each of its three tabs.

Rendering is the only way to see whether a tab label or a table row came out
legible — reading the code cannot tell you that the theme repainted it grey on
grey. Writes announcer_tab_<n>.png next to this file.

Driven from inside a real mainloop, not from a loop of update() calls: the
program hands its archiver results back with after() FROM A WORKER THREAD, and
those only arrive when the event loop is genuinely running. Pumping with
update() leaves the Halls read-out stuck on "reading...", which looks like a
bug in the program and is not one.

A shot that comes back as a photograph or as solid black is the LOCK SCREEN: a
locked session has no window to photograph. Unlock and run it again.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from PIL import ImageGrab                      # noqa: E402
import a                                        # noqa: E402

# ImageGrab takes the bbox in absolute screen coordinates and works out the
# virtual-desktop offset itself — do not subtract it here. This machine's
# virtual desktop starts at y = -174, and correcting for that by hand shifted
# every shot down by 174 px.

HALL_WAIT_MS = 20000       # the beam-fate channel does not exist; reads are slow


class Shooter:
    def __init__(self):
        self.app = a.ScreenTracker()
        # The saved geometry puts the window wherever it was last left, which
        # can be a monitor that is currently asleep — that grab comes back black.
        self.app.update()
        self.app._apply_geometry(self.app, "820x620+80+60")
        self.app.update_idletasks()
        print("window at", self.app.winfo_rootx(), self.app.winfo_rooty(),
              self.app.winfo_width(), "x", self.app.winfo_height())
        self.queue = list(enumerate(a.ScreenTracker._COND_TABS))

    def run(self):
        self.app.after(500, self._next)
        self.app.mainloop()

    def _next(self):
        if not self.queue:
            print("done")
            self.app.destroy()
            return
        i, (kind, title) = self.queue.pop(0)
        tag = f"{i}_{title.split()[0].lower()}"
        self.app._cond_frame.select(i)
        self.app.update_idletasks()
        if kind == "hall":
            self._await_readout(tag, HALL_WAIT_MS)
        else:
            self.app.after(400, lambda: self._shoot(tag))

    def _await_readout(self, tag, left_ms):
        """Photograph the Halls tab once its read-out has an answer.

        Waiting for "Beam:" rather than for "reading..." to go away: the tab's
        one-shot read is kicked off by the <<NotebookTabChanged>> event, which
        is delivered while events are being processed, not by the select() call
        itself. Immediately after selecting the tab the line still says "not
        read yet", which is not an answer either.
        """
        if "Beam:" in self.app._hall_readout.get() or left_ms <= 0:
            print("   readout:", self.app._hall_readout.get())
            self.app.after(300, lambda: self._shoot(tag))
            return
        self.app.after(200, lambda: self._await_readout(tag, left_ms - 200))

    def _shoot(self, tag):
        app = self.app
        app.update_idletasks()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        # A little margin so the window frame and the tab strip are both in.
        img = ImageGrab.grab(bbox=(x - 10, y - 40, x + w + 10, y + h + 10),
                             all_screens=True)
        path = HERE / f"announcer_tab_{tag}.png"
        img.save(path)
        print(f"{tag}: {img.size[0]}x{img.size[1]} -> {path.name}")
        app.after(200, self._next)


if __name__ == "__main__":
    Shooter().run()
