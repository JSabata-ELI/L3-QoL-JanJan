"""A whole auto run against a fake archive on the local disk.

Starts the real program, points the camera lookup at a temporary folder that a
writer thread keeps filling at about 2 frames a second, runs three 5 s cycles
and then checks what came out:

  * the cycles are exactly one interval apart,
  * the pictures inside one cycle are all within a second of that cycle's
    moment (the bug being fixed spread them over ten seconds),
  * every cycle appears in the one live preview window, which is never replaced.

Takes about 20 s. Needs a desktop session. Nothing is clicked or typed — the
same methods the buttons call are called directly.

Run:  python testing/test_auto_run_end_to_end.py
"""
import io
import re
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s  # noqa: E402

SEC = 1_000_000_000
CAMS = ["PAP1_NF", "PAP1_DF", "PAM5_NF", "PAM10_NF", "PAM9_NF", "PAM12_NF"]
INTERVAL = 5
CYCLES = 3
# The real archive's frames are stamped well behind this computer's clock (this
# PC runs seconds ahead of the facility and CPVA publishes about a second late).
# Without correcting for that, every camera looks half a minute too old and the
# whole run gets skipped — which is exactly what happened on the first live try.
SKEW = 28.5
FAILED = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(name)


class FakeArchive:
    """A folder per camera, filled with timestamped frames while the run goes on."""

    def __init__(self, root: Path):
        from PIL import Image
        self.root = root
        self.folders = {}
        for cam in CAMS:
            d = root / cam
            d.mkdir(parents=True, exist_ok=True)
            self.folders[cam] = d
        # A real (tiny) PNG: the preview has to be able to draw it.
        buf = io.BytesIO()
        Image.new("L", (96, 72), 120).save(buf, "PNG")
        self._png = buf.getvalue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._write, daemon=True)

    def start(self):
        self._write_once()          # something already there when cycle 1 fires
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _write_once(self):
        ts = time.time_ns() - int(SKEW * SEC)
        for cam, d in self.folders.items():
            (d / f"{cam}_{ts}.png").write_bytes(self._png)

    def _write(self):
        while not self._stop.wait(0.5):
            self._write_once()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="autorun_"))
    archive = FakeArchive(tmp / "archive")
    dest = tmp / "out"
    dest.mkdir(parents=True, exist_ok=True)
    archive.start()

    s._set_dpi_awareness()
    app = s.App()
    # Point the camera lookup at the fake archive; everything else is the real
    # program, including the grid, the queue and the preview window.
    app._resolve_cam_folders = lambda _t, cams: {c: archive.folders[c] for c in cams
                                                 if c in archive.folders}
    app.dest_dir.set(str(dest))
    app.run_name_var.set("")
    app.all_screens_var.set(False)
    for v in app.monitor_vars:
        v.set(False)
    app._programmatic_cam_update = True
    for cam, var in app.camera_vars.items():
        var.set(cam in CAMS)
    app._programmatic_cam_update = False
    app._auto_interval_var.set(INTERVAL)
    app._auto_cycles_var.set(CYCLES)
    app._preview_enabled.set(True)

    print(f"running {CYCLES} cycles every {INTERVAL}s…")
    app._start_auto_copy()
    app.after(int((INTERVAL * CYCLES + 4) * 1000), app.quit)
    app.mainloop()
    archive.stop()

    hist = app._auto_cycle_history
    win = app._auto_preview
    print()
    print("cycles")
    check(f"{CYCLES} cycles were recorded", len(hist) == CYCLES, f"got {len(hist)}")
    check("cycle numbers have no gaps",
          [e["cycle"] for e in hist] == list(range(1, len(hist) + 1)),
          str([e["cycle"] for e in hist]))
    gaps = [(hist[i + 1]["target_ns"] - hist[i]["target_ns"]) / SEC
            for i in range(len(hist) - 1)]
    check(f"cycle moments are exactly {INTERVAL}s apart",
          all(abs(g - INTERVAL) < 0.001 for g in gaps), str(gaps))
    check("the run stopped by itself", not app._auto_copy_active)

    print("pictures inside each cycle")
    for e in hist:
        stamps = []
        for f in e["files"]:
            m = re.search(r"__(\d{4}-\d{2}-\d{2}__\d{2}-\d{2}-\d{2})", f.name)
            if m:
                stamps.append(datetime.strptime(m.group(1), s.TS_FMT))
        check(f"cycle {e['cycle']}: all {len(CAMS)} cameras",
              len(e["files"]) == len(CAMS), f"got {len(e['files'])}")
        if stamps:
            spread = (max(stamps) - min(stamps)).total_seconds()
            check(f"cycle {e['cycle']}: pictures within 1 s of each other",
                  spread <= 1.0, f"spread {spread:.1f}s")
            # Measured against the archive's own clock, so the fixed skew is
            # taken out — what is checked is that each cycle really lands on its
            # own moment and not on a stale frame.
            behind = e["target_ns"] / SEC - SKEW - min(s_.timestamp() for s_ in stamps)
            check(f"cycle {e['cycle']}: taken from the cycle's own moment",
                  -1.0 <= behind <= 2.0, f"{behind:.1f}s behind")

    print("archive clock")
    check("the offset was measured", app._auto_offset_ns is not None)
    if app._auto_offset_ns is not None:
        off = app._auto_offset_ns / SEC
        check("it matches the archive's real skew", abs(off - SKEW) < 1.5, f"{off:.1f}s")
    gaps = []
    for i in range(len(hist) - 1):
        a = min(re.search(r"__(\d{4}-\d{2}-\d{2}__\d{2}-\d{2}-\d{2})", f.name).group(1)
                for f in hist[i]["files"])
        b = min(re.search(r"__(\d{4}-\d{2}-\d{2}__\d{2}-\d{2}-\d{2})", f.name).group(1)
                for f in hist[i + 1]["files"])
        gaps.append((datetime.strptime(b, s.TS_FMT) - datetime.strptime(a, s.TS_FMT)).total_seconds())
    check(f"the pictures themselves step forward by {INTERVAL}s per cycle",
          all(abs(g - INTERVAL) <= 1.0 for g in gaps), str(gaps))

    print("preview window")
    check("one preview window for the whole run",
          win is not None and win.winfo_exists())
    if win is not None:
        check("it lists every cycle", len(win._cycle_history) == len(hist))
        check("it is showing the last cycle", win.shown_cycle == hist[-1]["cycle"])
        check("its grid holds the last cycle's pictures",
              len(win._inner.winfo_children()) == len(hist[-1]["files"]))

    print()
    print("log tail:")
    for line in [l for l in app.log_text.get("1.0", "end").splitlines() if "[AUTO]" in l][-6:]:
        print("   " + line)

    app.destroy()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
