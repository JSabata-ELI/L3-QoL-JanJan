"""How long does a live frame take to reach the screen after it lands in the folder?

Every other live harness here asks "does the tile update at all". This one asks the
question the operator actually asked — "it used to be instant, now it takes about a
second" — and it measures the one number that answers it:

    latency = (moment the tile PAINTED that frame) - (moment the file was COMPLETE)

The two cases are deliberately separated, because they exercise different code:

  --write-ms 0      the file appears complete (one write, or a rename). The dir-watcher
                    event and the finished file coincide, so this is the best the
                    pipeline can do: one share read + one decode.
  --write-ms 150    the file is CREATED first and filled over the next 150 ms, which is
                    what a camera writing straight onto an SMB share does. The watcher
                    fires on FILE_ACTION_ADDED, i.e. on the empty file, so the immediate
                    read hits a truncated PNG and comes back a null QImage. Nothing in
                    the pipeline re-asks for that frame (see the note at
                    CAM_STUCK_RETRY_AFTER_S in is_t.py) — only the 500 ms stuck tick
                    does, after a 1.5 s threshold. That is the second the operator sees.
  --atomic          the writer builds the file under a .tmp name and renames it into
                    place. The frame is therefore never visible half-written, which is
                    the reference for "what it would cost the archiver to fix it".

Frames are written from a BACKGROUND thread on purpose: the writer's own sleeps would
otherwise block the Qt event loop and the watcher push would be handled only after the
file was already whole — measuring nothing.

What it found, and what `--before` reproduces (--write-ms 400 --read-ms 145, p50/p95):

    1 camera   1531 / 1734 ms  ->  62 /  63 ms   (_read_frame_bytes waits out the write)
    6 cameras  1305 / 1734 ms  ->  62 / 125 ms   (+ _live_resync_slaves_on_master_paint)

The two causes are independent. The first is a truncated read nothing re-asked for; the
second is a slave synced at the master's REQUEST time, so any camera whose file landed
during the master's own share read stayed a shot behind.

  python testing/bench_live_latency.py --cams 1 --write-ms 0
  python testing/bench_live_latency.py --cams 1 --write-ms 150
  python testing/bench_live_latency.py --cams 6 --write-ms 150 --read-ms 145
"""
import argparse
import os
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_common as B


def pump(app, seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.004)


class Writer(threading.Thread):
    """Writes one shot per camera, the way the archiver does, and stamps the moment
    each file became COMPLETE. Runs off the GUI thread — see the module docstring."""

    def __init__(self, folders, cam_names, first_ts, step_ns, shots,
                 period_s, write_ms, atomic, src_bytes):
        super().__init__(daemon=True, name="bench-writer")
        self._folders   = folders
        self._names     = cam_names
        self._first_ts  = first_ts
        self._step_ns   = step_ns
        self._shots     = shots
        self._period    = period_s
        self._write_ms  = write_ms
        self._atomic    = atomic
        self._src       = src_bytes
        # (cam_i, ts_ns) -> monotonic time the file was complete
        self.done: "dict[tuple[int, int], float]" = {}
        self.lock = threading.Lock()

    def _write_one(self, folder: Path, name: str, ts_ns: int):
        final = folder / f"{name}_{ts_ns:019d}.png"
        data  = self._src
        if self._atomic:
            tmp = folder / f".{name}_{ts_ns:019d}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, final)
            return
        if self._write_ms <= 0:
            final.write_bytes(data)
            return
        # Create first, then fill — the shape that produces a truncated PNG on disk.
        chunks = 4
        step   = max(1, len(data) // chunks)
        gap    = (self._write_ms / 1000.0) / (chunks - 1)
        with open(final, "wb", buffering=0) as fh:
            for i in range(chunks):
                end = len(data) if i == chunks - 1 else (i + 1) * step
                fh.write(data[i * step:end])
                fh.flush()
                if i < chunks - 1:
                    time.sleep(gap)

    def run(self):
        for k in range(self._shots):
            time.sleep(self._period)
            for ci, folder in enumerate(self._folders):
                ts = self._first_ts + (k + 1) * self._step_ns + ci
                self._write_one(folder, self._names[ci], ts)
                with self.lock:
                    self.done[(ci, ts)] = time.monotonic()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=1)
    ap.add_argument("--frames", type=int, default=60, help="history frames per camera")
    ap.add_argument("--shots", type=int, default=8, help="live arrivals to measure")
    ap.add_argument("--period", type=float, default=5.0,
                    help="seconds between arrivals (the real cameras run 3-5 s)")
    ap.add_argument("--write-ms", type=float, default=0.0,
                    help="how long the file stays half-written after it is created")
    ap.add_argument("--read-ms", type=float, default=0.0,
                    help="simulated share read latency per frame (real: 130-160)")
    ap.add_argument("--atomic", action="store_true",
                    help="writer renames a finished .tmp into place")
    ap.add_argument("--trace", action="store_true",
                    help="print every change of the frame each tile is showing")
    ap.add_argument("--before", action="store_true",
                    help="undo both live-latency fixes — no wait for a frame still "
                         "being written, and no slave re-sync on the master's paint — "
                         "for an A/B against the numbers in the module docstring")
    args = ap.parse_args()

    m = B.load_slider()
    if args.before:
        def plain_read(path):
            try:
                if path.stat().st_size > m._INMEM_READ_MAX_BYTES:
                    return None
                with open(path, "rb") as fh:
                    return fh.read()
            except Exception:
                return None
        m._read_frame_bytes = plain_read
    B.install_read_latency(m, args.read_ms)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="is_lat_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(
        tmp, args.cams, args.frames, hz=1.0 / max(0.2, args.period))
    folders = [lst[0] for lst in cam_folder_lists]
    src = next(p for p in sorted(folders[0].glob("*.png"))
               if p.stat().st_size > 1000).read_bytes()

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    if args.before:
        v._live_resync_slaves_on_master_paint = lambda *a, **k: None

    all_ts = [t for f in folders for p in f.glob("*.png")
              if (t := m.parse_unix_ns_from_name(p))]
    axis = (min(all_ts), max(all_ts))
    if args.cams == 1:
        v._ts_windows = None
        v._start_scan(list(cam_folder_lists[0]), axis_override=axis,
                      folder_label=str(folders[0]))
        ok = B.wait_for(lambda: bool(v.ts_list), 120.0)
    else:
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        ok = B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0)
    if not ok:
        print("scan did not finish")
        return 1

    newest  = max(all_ts)
    step_ns = int(args.period * 1e9)

    v._btn_auto_follow.setChecked(True)          # → _start_online_mode
    if not v._online_mode:
        print("live mode did not start")
        return 1
    print(f"cams={args.cams} write-ms={args.write_ms:.0f} read-ms={args.read_ms:.0f} "
          f"atomic={args.atomic} period={args.period:.1f}s")
    print(f"waiting out LIVE_START_GRACE_S ({m.LIVE_START_GRACE_S:.0f} s) …")
    pump(app, m.LIVE_START_GRACE_S + 1.0)

    wr = Writer(folders, cam_names, newest, step_ns, args.shots,
                args.period, args.write_ms, args.atomic, src)

    painted: "dict[tuple[int, int], float]" = {}
    trace: "list[tuple[float, int, int]]" = []
    last_shown = [0] * args.cams
    fails0 = list(v._cam_read_fail)
    stuck0 = getattr(v, "_cam_stuck_recoveries", 0)
    print(f"master camera: {v._per_cam_master_idx}   "
          f"per-cam rows: {len(v._per_cam_rows or [])}")
    t_start = time.monotonic()
    wr.start()
    deadline = time.monotonic() + args.period * (args.shots + 4) + 20.0
    while time.monotonic() < deadline:
        app.processEvents()
        for ci in range(args.cams):
            shown = m._at(v._cam_shown_ts_ns, ci, 0)
            if not shown:
                continue
            if shown != last_shown[ci]:
                last_shown[ci] = shown
                trace.append((time.monotonic() - t_start, ci, shown))
            with wr.lock:
                done_at = wr.done.get((ci, shown))
            if done_at is not None and (ci, shown) not in painted:
                painted[(ci, shown)] = time.monotonic() - done_at
        if len(painted) >= args.shots * args.cams and not wr.is_alive():
            break
        time.sleep(0.004)

    with wr.lock:
        written = len(wr.done)
    lat = sorted(painted.values())
    print(f"\nwritten {written}   painted {len(lat)}")
    if lat:
        print(f"latency  p50 {statistics.median(lat)*1000:7.0f} ms   "
              f"p95 {lat[min(len(lat)-1, int(0.95*len(lat)))]*1000:7.0f} ms   "
              f"max {lat[-1]*1000:7.0f} ms   min {lat[0]*1000:7.0f} ms")
        for ci in range(args.cams):
            mine = sorted(t for (c, _ts), t in painted.items() if c == ci)
            if mine:
                print(f"  {cam_names[ci]:<12} n={len(mine):<3} "
                      f"p50 {statistics.median(mine)*1000:7.0f} ms  "
                      f"max {mine[-1]*1000:7.0f} ms")
    if args.trace:
        print("\nwhat each tile showed, and when:")
        for t, ci, ts in trace:
            with wr.lock:
                done_at = wr.done.get((ci, ts))
            mine = "" if done_at is None else f"  (+{(t_start + t - done_at)*1000:.0f} ms)"
            print(f"  {t:7.3f}s  {cam_names[ci]:<12} {ts % 1_000_000_000_000:012d}{mine}")
    missing = written - len(lat)
    print(f"never painted: {missing}")
    print(f"read failures now: {list(v._cam_read_fail)} (was {fails0})   "
          f"stuck recoveries: {getattr(v, '_cam_stuck_recoveries', 0) - stuck0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
