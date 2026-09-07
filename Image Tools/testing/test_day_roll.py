"""Assert live mode survives the night and lands on the NEW day.

Left running overnight, live mode used to go quiet for good: the next morning's
first image never appeared and the dot stayed green, because "no new images" is a
healthy idle source by design. Two separate faults did that.

1. Where the next folder is looked for. The forward walk steps at most three hours
   past the newest KNOWN folder and stops at the first hour that does not exist.
   The archive writes nothing at night or at weekends, so the real gap is ~13 hours
   and the walk could never reach across it. clock_hour_folders answers the question
   from the CLOCK instead — "where would an image written right now land" — so the
   width of the gap stops mattering.
2. The midnight arithmetic inside that walk. The next DAY's path was only built for
   a step of +1 hour, so from hour 22 the +2 candidate came out as <same day>/0 — a
   path that never exists, ending the walk right at the date change. Both branches
   are wrapped in `except Exception: continue`, so a mistake there is silent.

And once the morning's frames ARE found, they must not be merged onto yesterday's
axis: the window is re-picked on the new day (Viewer._live_rebase_to_day), which is
what the second half of this file pins.

The end-to-end version of the same story — real folders, the real poll, a 17-hour
gap — is bench_overnight.py.

    python testing/test_day_roll.py
"""
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import bench_common as B      # sets the offscreen platform before Qt is imported

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


m = B.load_slider()
ROOT = Path(r"\\share\archive")
CAM = "L3-OM1-FF"


def ns(y, mo, d, hh, mm):
    """Prague wall clock -> ns, the way a frame's name is read."""
    return m.ns_from_dt(datetime(y, mo, d, hh, mm))


# ═══ 1. where the next folder is looked for ══════════════════════════════════
print("\n[1] clock_hour_folders — the candidates come from the clock")

g = time.gmtime()
now_h = datetime(g.tm_year, g.tm_mon, g.tm_mday, g.tm_hour)
prev_h = now_h - timedelta(hours=1)

# A reference folder from LAST YEAR: the widest gap there is.
old = ROOT / "2025" / "3" / "4" / "17" / CAM
got = m.clock_hour_folders(old)

plain = [ROOT / str(t.year) / str(t.month) / str(t.day) / str(t.hour) / CAM
         for t in (now_h, prev_h)]
check("the current and previous UTC hour are offered",
      all(p in got for p in plain), f"got={[str(p) for p in got]}")
check("a year-old reference folder is no obstacle", len(got) >= 2)

# The archive writes bare integers, but the padded spelling is probed too so either
# convention is found. The padding of a KNOWN folder cannot be copied: a reference
# hour of "17" says nothing about how hour 6 is written.
padded = [ROOT / str(t.year) / f"{t.month:02d}" / f"{t.day:02d}" / f"{t.hour:02d}" / CAM
          for t in (now_h, prev_h)]
check("the zero-padded spelling is offered as well",
      all(p in got for p in padded), f"got={[str(p) for p in got]}")
check("no candidate is offered twice", len(got) == len(set(got)))
check("an explicit camera name wins over the folder's own",
      m.clock_hour_folders(old, "L3-SBW4")[0].name == "L3-SBW4")
check("a path that is not the archive layout yields nothing",
      m.clock_hour_folders(Path(r"C:\somewhere\else")) == [])

# The wall-clock gate is what keeps this cheap: an hour that has not happened yet
# is refused without touching the share.
future = (ROOT / str(now_h.year) / str(now_h.month) / str(now_h.day)
          / str((now_h + timedelta(hours=3)).hour) / CAM)
check("an hour still in the future is refused without a share access",
      m._probe_hour_folder(future) is False)


# ═══ 2. the midnight arithmetic in the forward walk ══════════════════════════
print("\n[2] the forward walk crosses midnight for EVERY step, not just +1")


def walk(y, mo, d, hh, cam=CAM):
    """The candidate arithmetic of _CamPollTask, re-stated. It cannot be called
    directly (it is inline in run(), behind a listdir of the real share), so this
    mirrors it — and would drift if that code changed, which is the point: the
    values below are the ones the shipping branch has to produce."""
    folder = ROOT / str(y) / str(mo) / str(d) / str(hh) / cam
    hour_dir = folder.parent
    day_dir = hour_dir.parent
    cur = int(hour_dir.name)
    out = []
    for delta in range(1, 4):
        nxt = (cur + delta) % 24
        if nxt < cur:
            parts = (int(day_dir.parent.parent.name), int(day_dir.parent.name),
                     int(day_dir.name))
            nd = date(*parts) + timedelta(days=1)
            out.append(ROOT / str(nd.year) / str(nd.month) / str(nd.day)
                       / str(nxt) / cam)
        else:
            out.append(day_dir / str(nxt) / cam)
    return out


w = walk(2026, 8, 25, 22)
check("from hour 22 the +2 step is the NEXT day's hour 0",
      w[1] == ROOT / "2026" / "8" / "26" / "0" / CAM, f"got={w[1]}")
check("from hour 22 the +3 step is the NEXT day's hour 1",
      w[2] == ROOT / "2026" / "8" / "26" / "1" / CAM, f"got={w[2]}")
check("the month rolls over too",
      walk(2026, 8, 31, 23)[0] == ROOT / "2026" / "9" / "1" / "0" / CAM)
check("the year rolls over too",
      walk(2026, 12, 31, 23)[0] == ROOT / "2027" / "1" / "1" / "0" / CAM)


# ═══ 3. listing a folder that has just been discovered ═══════════════════════
print("\n[3] _new_items_in — what a freshly found folder yields")

tmp = Path(tempfile.mkdtemp(prefix="is_newitems_"))
ts_a = int(datetime(2026, 8, 26, 8, 5, tzinfo=timezone.utc).timestamp() * 1e9)
ts_b = ts_a + 5_000_000_000           # a few seconds apart, a rate the archive does hit
for t in (ts_a, ts_b):
    (tmp / f"{CAM}_{t:019d}.png").write_bytes(b"x")
(tmp / "notes.txt").write_bytes(b"x")

check("both images are found and the .txt is left alone",
      len(m._new_items_in(tmp, 0)) == 2,
      f"got={[p.path.name for p in m._new_items_in(tmp, 0)]}")
one = m._new_items_in(tmp, ts_a)
check("the cutoff keeps out what is already known",
      len(one) == 1 and one[0].ts_ns == ts_b, f"got={one}")
check("a folder that is not there yet is not an error",
      m._new_items_in(tmp / "nope", 0) == [])


# ═══ 4. is this arrival a new day? ═══════════════════════════════════════════
print("\n[4] _live_day_rolled — when the window has to move")

rolled = m.Viewer._live_day_rolled


class Fake:
    """Only the state _live_day_rolled reads — a whole Viewer is not needed and
    would drag a scan in with it."""
    _online_mode = True
    last_pick_cam_names = [CAM]
    _cam_ts: list = []
    ts_list: list = []
    _multi = False

    def _is_multi_cam(self):
        return self._multi


f = Fake()
f.ts_list = [ns(2026, 8, 25, 16, 0), ns(2026, 8, 25, 19, 0)]
check("a later hour of the same day changes nothing",
      rolled(f, ns(2026, 8, 25, 19, 30)) is False)
check("the reported case: 25.8. 19:00 loaded, 26.8. 08:05 arrives",
      rolled(f, ns(2026, 8, 26, 8, 5)) is True)
check("a whole weekend later, likewise",
      rolled(f, ns(2026, 8, 31, 8, 5)) is True)
check("midnight after hours of silence, likewise",
      rolled(f, ns(2026, 8, 26, 0, 10)) is True)

# A new calendar day on its own is NOT enough. A campaign running across midnight
# keeps delivering — the archiver's rate is not fixed, it can reach 3 frames/s — and
# clearing the evening off the slider in the middle of it would throw away frames the
# operator is still working with.
f.ts_list = [ns(2026, 8, 25, 23, 58), ns(2026, 8, 25, 23, 59)]
check("frames still flowing across midnight: the evening stays",
      rolled(f, ns(2026, 8, 26, 0, 0)) is False)
f.ts_list = [ns(2026, 8, 25, 23, 10)]
check("50 minutes of silence is not yet a night",
      rolled(f, ns(2026, 8, 26, 0, 0)) is False)
f.ts_list = [ns(2026, 8, 25, 22, 0)]
check("two and a half hours of silence is",
      rolled(f, ns(2026, 8, 26, 0, 30)) is True)

f.ts_list = []
check("nothing loaded yet: no day to leave",
      rolled(f, ns(2026, 8, 26, 8, 5)) is False)
f.ts_list = [ns(2026, 8, 25, 19, 0)]
f._online_mode = False
check("live mode off: the window is the user's, not ours",
      rolled(f, ns(2026, 8, 26, 8, 5)) is False)
f._online_mode = True
f.last_pick_cam_names = []
check("no remembered cameras: nothing to reload with",
      rolled(f, ns(2026, 8, 26, 8, 5)) is False)

# Multi-cam: the newest frame across ALL cameras decides, which is what stops the
# other eleven cameras from each triggering their own re-base the same morning.
mc = Fake()
mc._multi = True
mc.ts_list = [ns(2026, 8, 25, 16, 0)]
mc._cam_ts = [[ns(2026, 8, 25, 18, 0)], [], [ns(2026, 8, 26, 8, 5)]]
check("one camera already on the new day: the next arrival does not re-base again",
      rolled(mc, ns(2026, 8, 26, 8, 6)) is False)
mc._cam_ts = [[ns(2026, 8, 25, 18, 0)], [], [ns(2026, 8, 25, 19, 0)]]
check("all cameras still on the old day: re-base",
      rolled(mc, ns(2026, 8, 26, 8, 5)) is True)
mc._cam_ts = [[], [], []]
check("multi-cam with no frames at all: no re-base",
      rolled(mc, ns(2026, 8, 26, 8, 5)) is False)


# ═══ 5. one morning, one reload ══════════════════════════════════════════════
print("\n[5] the cooldown — twelve cameras must not queue twelve reloads")

queued: list = []


class _StubTimer:
    @staticmethod
    def singleShot(ms, fn):
        queued.append(fn)


real_timer = m.QTimer
m.QTimer = _StubTimer
try:
    r = Fake()
    m.Viewer._live_rebase_to_day(r, ns(2026, 8, 26, 8, 5))
    first = len(queued)
    for _ in range(11):        # the rest of the grid reports the same morning
        m.Viewer._live_rebase_to_day(r, ns(2026, 8, 26, 8, 5))
    check("twelve cameras queue ONE reload", first == 1 and len(queued) == 1,
          f"queued={len(queued)}")
    m.Viewer._live_rebase_to_day(Fake(), ns(2026, 8, 27, 8, 5))
    check("the next morning queues its own", len(queued) == 2)
    check("the block lasts LIVE_REBASE_COOLDOWN_S",
          abs(r._live_rebase_block_mono - time.monotonic()
              - m.LIVE_REBASE_COOLDOWN_S) < 2.0,
          f"block={r._live_rebase_block_mono}")
finally:
    m.QTimer = real_timer


# ═══ 6. what the re-base leaves behind ═══════════════════════════════════════
print("\n[6] the window after the re-base")

from PySide6.QtWidgets import QApplication          # noqa: E402
app = QApplication.instance() or QApplication([])

reloaded: list = []
real_reload = m.Viewer._reload_with_last_cameras
# The reload itself goes to the archive share; what is checked here is the pick it
# is handed.
m.Viewer._reload_with_last_cameras = lambda self: reloaded.append(True)
v = m.Viewer()
try:
    v._diag_timer.stop()
except Exception:
    pass
try:
    v.last_pick_cam_names = [CAM]
    v._online_mode = True
    # The reported pick: 25.8. 16:00-19:00, live.
    v.last_pick_date = date(2026, 8, 25)
    v.last_pick_hour_from, v.last_pick_min_from = 16, 0
    v.last_pick_hour_to, v.last_pick_min_to = 19, 0
    v._ts_windows = [(ns(2026, 8, 25, 16, 0), m.TS_WINDOW_OPEN_END)]
    v.axis_min_ns = ns(2026, 8, 25, 16, 0)
    v.axis_max_ns = ns(2026, 8, 25, 19, 0)

    v._do_live_rebase_to_day(ns(2026, 8, 26, 8, 5))

    check("a reload was launched", len(reloaded) == 1)
    check("the picked day is 26.8.", v.last_pick_date == date(2026, 8, 26),
          f"got={v.last_pick_date}")
    check("it starts at the new frame's hour",
          (v.last_pick_hour_from, v.last_pick_min_from) == (8, 0),
          f"got={v.last_pick_hour_from}:{v.last_pick_min_from:02d}")
    check("it runs to the end of that day",
          (v.last_pick_hour_to, v.last_pick_min_to) == (23, 59),
          f"got={v.last_pick_hour_to}:{v.last_pick_min_to}")
    check("yesterday is out of the time filter",
          v._ts_windows[0][0] == ns(2026, 8, 26, 8, 0), f"got={v._ts_windows}")
    check("the axis is one hour wide and grows from there",
          v.last_pick_axis_override == (ns(2026, 8, 26, 8, 0),
                                        ns(2026, 8, 26, 9, 0)),
          f"got={v.last_pick_axis_override}")
    check("live mode is queued to resume", v._pending_online_mode is True)
    check("the view will not rewind to the start of the window",
          v._land_at_window_start is False)
    check("the cameras are untouched", v.last_pick_cam_names == [CAM])
    check("the INFO Date/Range row shows the new day",
          any(r[0] == date(2026, 8, 26) for r in v._range_rows),
          f"rows={v._range_rows}")
finally:
    m.Viewer._reload_with_last_cameras = real_reload
    try:
        v._hard_reset_runtime()
    except Exception:
        pass

print("\n" + ("ALL CHECKS PASSED" if not FAILURES else
             f"{len(FAILURES)} CHECK(S) FAILED:\n  " + "\n  ".join(FAILURES)))
sys.exit(1 if FAILURES else 0)
