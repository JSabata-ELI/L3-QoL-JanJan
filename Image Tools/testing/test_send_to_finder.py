"""Assert "Send to Image Finder" carries the moment, the camera and the day.

The Image Slider and the Shot Finder both hand the Image Finder a MOMENT — never
a file. That is the whole trick: the Finder looks the frames up itself, so a
moment scrubbed to between two frames, or a day whose picture was never loaded,
is still a valid thing to send. What has to hold:

  * the Finder's public handoff (`open_moments`) picks the days of the moments in
    the Time window, with a window that CONTAINS them (a pushed moment used to be
    unreachable if the tab was left on another day),
  * it re-reads the camera list for those days and only THEN ticks the sent
    cameras — the folders of a day the tab has never looked at are not known
    before that scan comes back,
  * a camera that is not on those days leaves the tab's own pick alone instead of
    emptying it,
  * the Slider sends the moment on screen with the cameras loaded there,
  * the Shot Finder follows its Act-on selector: one moment per day row for
    "Whole day(s)", the picked shots for "Selected shot".

Runs offscreen against a synthetic local archive tree — no share, no archiver:

    python testing/test_send_to_finder.py
"""
import importlib.util
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []

DAY = date(2026, 8, 17)
HOUR = 10                     # Prague wall clock
CAMS = ["C03-040-PTM11WNF-_-IMG", "C03-041-PAM1FF-_-IMG"]
FRAME_STEP_S = 5
FRAMES = 24


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def load_finder():
    """Load if_t.py as `image_finder`, the way main.py does — slider FIRST, so the
    Finder borrows that instance instead of exec'ing is_t.py a second time."""
    B.load_slider()
    if "image_finder" in sys.modules:
        return sys.modules["image_finder"]
    argv, sys.argv = sys.argv, ["if_t.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "image_finder", str(B.HERE / "if_t.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["image_finder"] = m
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = argv


def prague_ns(day: date, hour: int, minute: int = 0) -> int:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Europe/Prague")
    return int(datetime(day.year, day.month, day.day, hour, minute, 0,
                        tzinfo=tz).timestamp() * 1e9)


def build_archive(root: Path, sf) -> "list[int]":
    """<root>/<year>/<month>/<day>/<UTC hour>/<camera>/<unix ns>.png

    The hour folder is named after the UTC hour while the moment is Prague wall
    time; the conversion is the resolver's own, so no DST rule is assumed here.
    """
    import numpy as np
    from PIL import Image as PilImage

    hour_utc = sf._folder_hour_from_prague(HOUR, DAY)
    base = prague_ns(DAY, HOUR)
    stamps = [base + i * FRAME_STEP_S * 1_000_000_000 for i in range(FRAMES)]
    arr = np.zeros((40, 60), dtype=np.uint16)
    arr[10:30, 15:45] = 2048
    img = PilImage.fromarray(arr)
    for cam in CAMS:
        d = root / str(DAY.year) / str(DAY.month) / str(DAY.day) / str(hour_utc) / cam
        d.mkdir(parents=True, exist_ok=True)
        for ts in stamps:
            img.save(str(d / f"{cam}_-_{ts}.png"))
    return stamps


class _FakeMsg:
    """Every popup, recorded instead of shown.

    A real QMessageBox waits for a click, and offscreen there is nobody to click
    it — the test would simply hang. "It told the user" is also worth asserting,
    so the calls are kept.
    """
    said: "list[str]" = []

    @staticmethod
    def _rec(parent, title, text, *a, **k):
        _FakeMsg.said.append(f"{title}: {text}")
        return _FakeMsg.StandardButton.Yes

    information = _rec
    warning = _rec
    critical = _rec
    question = _rec

    class StandardButton:
        Yes = 1
        No = 0
        Ok = 1


class _StubTabs:
    """Just enough QTabWidget for a handoff: which tab it was told to show."""
    def __init__(self):
        self.shown = None

    def setCurrentIndex(self, i):
        self.shown = i


class _StubFinder:
    """Records what a sender handed over, and says it took it."""
    def __init__(self, answer=True):
        self.calls = []
        self.answer = answer

    def open_moments(self, moments_ns, cam_names=None):
        self.calls.append((list(moments_ns), list(cam_names or [])))
        return self.answer


# ── the Finder's own end of the handoff ───────────────────────────────────────
def check_finder_receives(m, stamps):
    from PySide6.QtCore import QDate

    w = m.ImageFinderWidget()
    ts = stamps[4]                              # 10:00:20 Prague
    ok = w.open_moments([ts], [CAMS[0]])
    check("the handoff is taken", ok is True)

    days = [d.toString("yyyy-MM-dd") for d in w._selected_days]
    check("the day of the moment is the day picked",
          days == [QDate(DAY.year, DAY.month, DAY.day).toString("yyyy-MM-dd")],
          str(days))
    seg = w._segments[0]
    lo = seg.h_from * 60 + seg.m_from
    hi = seg.h_to * 60 + seg.m_to
    check("the time window contains the moment",
          lo <= HOUR * 60 <= hi, f"{seg.h_from:02d}:{seg.m_from:02d}"
                                 f"–{seg.h_to:02d}:{seg.m_to:02d}")
    check("and it is the pushed-moment pad, not a whole day",
          (hi - lo) <= 2 * w.PUSHED_MOMENT_PAD_MIN + 1, f"{hi - lo} min")

    B.wait_for(lambda: bool(w._cams), timeout_s=20.0)
    check("the camera list of that day was read", len(w._cams) == len(CAMS),
          f"{len(w._cams)} camera(s)")
    picked = [c[0] for c in w._checked_cameras()]
    check("only the sent camera is ticked", picked == [CAMS[0]], str(picked))
    check("the moment is the one on the wall", w._moment_ns == ts,
          f"{w._moment_ns} vs {ts}")
    B.wait_for(lambda: bool(w._moment_items), timeout_s=30.0)
    check("a frame was found for it", len(w._moment_items) == 1,
          f"{len(w._moment_items)} tile(s)")

    # A camera that was not archiving on that day must not empty the pick.
    w.open_moments([stamps[6]], ["C09-999-NOTTHERE-_-IMG"])
    B.wait_for(lambda: w._pushed_moments is None, timeout_s=20.0)
    still = [c[0] for c in w._checked_cameras()]
    check("an unknown camera leaves the tab's own pick alone",
          still == [CAMS[0]], str(still))
    check("and the new moment is still shown", w._moment_ns == stamps[6],
          f"{w._moment_ns} vs {stamps[6]}")

    check("no moment, no handoff", w.open_moments([]) is False)
    w.deleteLater()


# ── the Slider's end ─────────────────────────────────────────────────────────
def check_slider_sends(stamps):
    sl = sys.modules["image_slider"]
    v = sl.Viewer()
    fi, tabs = _StubFinder(), _StubTabs()
    v._finder_ref, v._tab_widget, v._finder_tab_idx = fi, tabs, 0

    # Nothing loaded: it must say so rather than send an empty moment.
    _FakeMsg.said.clear()
    v._send_to_image_finder()
    check("with nothing loaded nothing is sent", fi.calls == [], str(fi.calls))
    check("and it says why instead of sending an empty moment",
          any("No image loaded" in s for s in _FakeMsg.said), str(_FakeMsg.said))

    v._cam_names = list(CAMS)
    v.play_time_ns = stamps[4] + 2_000_000_000     # scrubbed between two frames
    v._send_to_image_finder()
    check("the moment on screen is what crosses over",
          fi.calls and fi.calls[0][0] == [stamps[4] + 2_000_000_000],
          str(fi.calls))
    check("the cameras loaded here go with it",
          fi.calls and fi.calls[0][1] == CAMS, str(fi.calls))
    check("and the Image Finder tab is brought to the front", tabs.shown == 0,
          str(tabs.shown))
    v.deleteLater()


# ── the Shot Finder's end ────────────────────────────────────────────────────
def day_result(sf, day, cam, hour):
    """One "ok" day row, the shape _on_day_result is fed by the search worker."""
    tz = sf.PRAGUE if sf.PRAGUE else timezone.utc
    dt = datetime(day.year, day.month, day.day, hour, 0, tzinfo=tz)
    row = {"_dt": dt.replace(tzinfo=None), "_ns": int(dt.timestamp() * 1e9),
           "sbw4": "10.000"}
    return {
        "day": day, "cam": cam, "status": "ok", "reason": "",
        "best_row": row, "rows_in_tol": [row], "col": "sbw4",
        "actual": 10.0, "diff": 0.0, "target_csv": 10.0,
        "hour_folder": None, "folder_path": None, "img_path": None,
        "per_col": {"sbw4": [row]},
        "col_meta": {"sbw4": {"source": "api", "status": "ok"}},
        "search_cols": ["sbw4"], "extra_cols": [],
        "criteria_csv": [{"col": "sbw4", "target_csv": 10.0, "target_ui": 10.0,
                          "tol_ui": 1.0, "tol_csv": 1.0}],
        "display_vals": {"sbw4": ("10.000", "ok")},
    }


def check_shot_finder_sends(sf):
    w = sf.ShotFinderWidget()
    fi, tabs = _StubFinder(), _StubTabs()
    w._finder_ref, w._tab_widget, w._finder_tab_idx = fi, tabs, 0

    days = [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]
    w._run_wants_images = False
    w._rebuild_result_tabs([CAMS[0]])
    for i, d in enumerate(days):
        w._on_day_result(day_result(sf, d, CAMS[0], 10 + i))
    w._on_search_done()

    check("the button is live once the days are in",
          w._btn_open_finder.isEnabled())
    w._scope_cb.setCurrentIndex(0)      # Whole day(s)
    w._on_send_to_finder()
    want = [prague_ns(d, 10 + i) for i, d in enumerate(days)]
    check("every day row sends one moment",
          fi.calls and fi.calls[0][0] == want, str(fi.calls[:1]))
    check("with the camera whose tab is in front",
          fi.calls and fi.calls[0][1] == [CAMS[0]], str(fi.calls[:1]))
    check("and the Image Finder tab is brought to the front", tabs.shown == 0,
          str(tabs.shown))

    # A selection narrows it to those rows, the way Send / Save already work.
    fi.calls.clear()
    w._table.selectRow(1)
    w._on_send_to_finder()
    check("a selected row sends only that day's moment",
          fi.calls and fi.calls[0][0] == [want[1]], str(fi.calls[:1]))
    w.deleteLater()


def main():
    m = load_finder()
    sl = sys.modules["image_slider"]
    sf = m._get_shot_finder_module()

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    tmp = Path(tempfile.mkdtemp(prefix="send_to_finder_"))
    root = tmp / "cpva-image-2026"
    stamps = build_archive(root, sf)

    # The two seams this test needs: where the day scan looks for camera folders,
    # and where the frame resolver looks for a year's archive.
    orig_base, orig_root = m.IMAGES_ROOT_BASE, sl.container_root_for_year
    m.IMAGES_ROOT_BASE = str(tmp)
    sl.container_root_for_year = lambda year: root
    # No popup may wait for a click in here — see _FakeMsg.
    for mod in (m, sl, sf):
        mod.QMessageBox = _FakeMsg
    try:
        print("Image Finder receives:")
        check_finder_receives(m, stamps)
        print("Image Slider sends:")
        check_slider_sends(stamps)
        print("Shot Finder sends:")
        check_shot_finder_sends(sf)
    finally:
        m.IMAGES_ROOT_BASE = orig_base
        sl.container_root_for_year = orig_root

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
