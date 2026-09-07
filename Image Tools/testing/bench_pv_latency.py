"""How long after the archiver publishes a shot's sample does the number appear?

bench_pv_live_multi.py already pins that the number arrives AT ALL and describes the
right frame. It cannot measure the delay, because it calls cpva.invalidate() the moment
it publishes — which hands the client knowledge it does not have in the field. Here the
panel has to FIND the new sample by itself, through the day cache's TTL and the wait
retry ladder, which is where the delay actually lives.

Two numbers come out, and they have to be read together:

  publish → shown    the latency the operator sees, minus the archiver's own ~1 s
                     publication delay (which nothing on this side can shorten).
  requests           tail queries actually sent. Any change that shortens the latency
                     by asking more often has to be judged against this.

  python testing/bench_pv_latency.py
  python testing/bench_pv_latency.py --shots 12 --period 2.0
"""
import argparse
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_common as B

SHARE_READ_MS = 145.0     # measured on \\users-L3 (see _open_reader)


def pump(app, seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.004)


def write_frame(folder: Path, cam: str, ts_ns: int) -> Path:
    dst = folder / f"{cam}_{ts_ns:019d}.png"
    src = next(f for f in sorted(folder.glob("*.png")) if f.stat().st_size > 1000)
    shutil.copyfile(src, dst)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cams", type=int, default=2)
    ap.add_argument("--shots", type=int, default=8)
    ap.add_argument("--period", type=float, default=5.0,
                    help="seconds between shots")
    ap.add_argument("--publish-lag", type=float, default=1.0,
                    help="archiver publication delay (measured p50 ~0.9 s)")
    ap.add_argument("--trace", action="store_true",
                    help="print every fetch the panel starts, and what it aimed at")
    ap.add_argument("--before", action="store_true",
                    help="restore the pre-fix wait behaviour — the ladder doubling from "
                         "400 ms and routed through the paced refresh gate — for an A/B")
    args = ap.parse_args()

    m = B.load_slider()
    cpva = m.cpva
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # ── fake archiver: samples become visible only when their lag has elapsed ─────
    lock = threading.Lock()
    published: "list[tuple[int, float]]" = []
    requests = [0]
    # sample ts_ns -> monotonic time it became readable, filled by the publisher
    published_at: "dict[int, float]" = {}

    def fake_fetch_values_ex(channel, start_ns, end_ns, *, timeout=None,
                             try_value_suffix=True):
        with lock:
            requests[0] += 1
            return ([s for s in published if start_ns <= s[0] <= end_ns], channel)

    cpva.fetch_values_ex = fake_fetch_values_ex
    cpva.fetch_values = lambda ch, a, b, **k: fake_fetch_values_ex(ch, a, b)[0]

    step_ns = int(args.period * 1e9)
    n_pre = 5
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - (n_pre + 1) * step_ns

    tmp = Path(tempfile.mkdtemp(prefix="is_pvlat_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(
        tmp, args.cams, n_pre, hz=1.0 / args.period, start_ns=start_ns)
    folders = [lst[0] for lst in cam_folder_lists]

    pre_ts = sorted(t for p in folders[0].glob("*.png")
                    if (t := m.parse_unix_ns_from_name(p)))
    with lock:
        published += [(t + 25_000_000, 10.0 + i) for i, t in enumerate(pre_ts)]
    cpva.invalidate()
    cpva.invalidate_lookback()

    B.install_read_latency(m, SHARE_READ_MS)

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()
    if args.before:
        # The wait timer is connected lazily, so replacing the bound method here is
        # enough to get the pre-fix routing; the ladder reads its two constants at use
        # time, so putting them back is enough for the rest.
        v._pv_retry_fetch_now = v._pv_trigger_fetch
        m.PV_ARCHIVER_RETRY_MS_MIN = 400
        m.PV_ARCHIVER_FINE_WAIT_S = 0.0

    axis = (pre_ts[0], pre_ts[-1])
    v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
    if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
        print("scan did not finish")
        return 1
    v._btn_auto_follow.setChecked(True)          # → _start_online_mode
    if not v._online_mode:
        print("live mode did not start")
        return 1
    print(f"cams={args.cams} shots={args.shots} period={args.period:.1f}s "
          f"publish-lag={args.publish_lag:.1f}s   share read {SHARE_READ_MS:.0f} ms")
    fetch_log: "list[tuple[float, int, int, bool]]" = []
    if args.trace:
        _orig_now = v._pv_trigger_fetch_now

        def traced_now():
            before = requests[0]
            fresh = bool(getattr(v, "_pv_fetch_fresh", False))
            _orig_now()
            fetch_log.append((time.monotonic(), v._pv_fetch_ts or 0,
                              requests[0] - before, fresh))
        v._pv_trigger_fetch_now = traced_now

    pump(app, 2.0)
    req0 = requests[0]

    # ── shots, fired from a background thread so the GUI keeps running ───────────
    stop = threading.Event()

    def shooter():
        for k in range(args.shots):
            if stop.wait(args.period):
                return
            ts = int(time.time() * 1e9)
            for ci, folder in enumerate(folders):
                write_frame(folder, cam_names[ci], ts)
            value = 100.0 + k
            if stop.wait(args.publish_lag):
                return
            with lock:
                published.append((ts + 25_000_000, value))
                published.sort()
                published_at[ts] = time.monotonic()

    th = threading.Thread(target=shooter, daemon=True, name="bench-shooter")
    th.start()

    lat: "list[float]" = []
    seen: "set[int]" = set()
    deadline = time.monotonic() + (args.period + args.publish_lag) * (args.shots + 3) + 30.0
    while time.monotonic() < deadline:
        app.processEvents()
        src = v._pv_last_good_ts.get("PTM1")
        if src and src not in seen:
            with lock:
                at = published_at.get(src)
            if at is not None:
                seen.add(src)
                lat.append(time.monotonic() - at)
        if len(lat) >= args.shots and not th.is_alive():
            break
        time.sleep(0.004)
    stop.set()

    used = requests[0] - req0
    print(f"\nshots {args.shots}   numbers shown {len(lat)}   tail requests {used}")
    if lat:
        s = sorted(lat)
        print(f"publish -> shown  p50 {statistics.median(s)*1000:7.0f} ms   "
              f"p95 {s[min(len(s)-1, int(0.95*len(s)))]*1000:7.0f} ms   "
              f"max {s[-1]*1000:7.0f} ms   min {s[0]*1000:7.0f} ms")
    print(f"requests per shot {used / max(1, args.shots):.1f}")
    if args.trace:
        with lock:
            pubs = sorted(published_at.items(), key=lambda kv: kv[1])
        print("\nfetches, timed against the publication they were waiting for:")
        for ts, at in pubs:
            print(f"  shot {ts % 1_000_000_000_000:012d} published at {at:.3f}")
            for t, aim, _n, fresh in fetch_log:
                if -2.5 < (t - at) < 2.5:
                    tag = "retry" if fresh else "paced"
                    hit = "  <== this shot" if aim == ts else ""
                    print(f"      {t - at:+7.3f}s {tag} aimed at "
                          f"{aim % 1_000_000_000_000:012d}{hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
