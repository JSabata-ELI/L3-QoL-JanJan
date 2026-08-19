"""Assert the PV panel follows the SHOT in LIVE MULTI-CAMERA mode.

bench_pv_wait.py pins the same promise on the single-camera path, where
_pv_current_ts answers from `items[current_idx]` — the frame the app has DECIDED to
show. Multi-cam answers from `_cam_shown_ts_ns`, the frame actually PAINTED on the
master tile, and in live mode the fetch is triggered at REQUEST time — before the
share read that paints it has finished. That is a different question and it is the one
the operator asked: after a single shot, do the numbers describe THIS shot, or do they
only catch up when the next shot is fired?

Runs offscreen against synthetic local files and a fake archiver transport — no share,
no network. The share's 130-160 ms read is simulated (bench_common.install_read_latency)
because without it the paint lands within a millisecond of the request and a
request-time/paint-time race cannot show up at all.

  python bench_pv_live_multi.py
"""
import os
import shutil
import tempfile
import time
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []

SHARE_READ_MS = 145.0     # measured on \\users-L3 (see _open_reader)
PUBLISH_LAG_S = 1.0       # measured archiver publication delay, p50 ~0.9 s


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def pump(app, seconds: float):
    """Spin the real event loop — the live poll timer must actually run."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def write_frame(folder: Path, cam: str, ts_ns: int) -> Path:
    """One more frame with a valid 19-digit ns name, copied from an existing PNG."""
    dst = folder / f"{cam}_{ts_ns:019d}.png"
    src = next(f for f in sorted(folder.glob("*.png")) if f.stat().st_size > 1000)
    shutil.copyfile(src, dst)
    return dst


def main():
    m = B.load_slider()
    cpva = m.cpva
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # ── fake archiver whose published set we control ──────────────────────────────
    published: "list[tuple[int, float]]" = []

    def fake_fetch_values_ex(channel, start_ns, end_ns, *, timeout=None,
                             try_value_suffix=True):
        return ([s for s in published if start_ns <= s[0] <= end_ns], channel)

    cpva.fetch_values_ex = fake_fetch_values_ex
    cpva.fetch_values = lambda ch, a, b, **k: fake_fetch_values_ex(ch, a, b)[0]

    def republish():
        cpva.invalidate()
        cpva.invalidate_lookback()

    # Shots 5 s apart — the reported cadence (one shot, then look at the screen), and
    # far enough apart that the panel's own rate limit can never be what saves it.
    step_ns = 5_000_000_000
    n_pre = 5
    now_ns = int(time.time() * 1e9)
    start_ns = now_ns - (n_pre + 1) * step_ns

    tmp = Path(tempfile.mkdtemp(prefix="is_pvlive_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(
        tmp, 2, n_pre, hz=1.0 / 5.0, start_ns=start_ns)
    folders = [lst[0] for lst in cam_folder_lists]

    # Every pre-existing MASTER frame is published, so the panel starts settled.
    pre_ts = sorted(t for p in folders[0].glob("*.png")
                    if (t := m.parse_unix_ns_from_name(p)))
    published += [(t + 25_000_000, 10.0 + i) for i, t in enumerate(pre_ts)]
    republish()

    # After the data is written, before anything reads it.
    B.install_read_latency(m, SHARE_READ_MS)
    print(f"simulating {SHARE_READ_MS:.0f} ms per frame read (the share)")

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()

    def pv_src_ts():
        return v._pv_last_good_ts.get("PTM1")

    try:
        axis = (pre_ts[0], pre_ts[-1])
        v._start_multi_cam_scan(cam_names, cam_folder_lists, axis, False, None)
        if not B.wait_for(lambda: all(len(c) for c in (v._cam_ts or [[]])), 120.0):
            print("scan did not finish")
            return 1
        check("two cameras scanned", len(v._cam_ts) == 2,
              f"{[len(c) for c in v._cam_ts]} frames per camera")

        v._btn_auto_follow.setChecked(True)          # → _start_online_mode
        check("live mode started", v._online_mode)
        master = v._per_cam_master_idx
        check("first camera is the master", master == 0, f"master={master}")
        pump(app, 2.0)

        def fire_shot(value: float, publish_lag_s: float, label: str):
            """Write one new frame to BOTH cameras, publish the master's sample after
            `publish_lag_s`, and let the app run — no user interaction at all."""
            ts = int(time.time() * 1e9)
            for ci, folder in enumerate(folders):
                write_frame(folder, cam_names[ci], ts)
            print(f"\n[{label}] shot at {time.strftime('%H:%M:%S')} "
                  f"(sample published {publish_lag_s:.1f} s later, value {value})")
            got_frame = B.wait_for(lambda: ts in (v._cam_ts[0] or []), 20.0)
            check(f"{label}: the new frame is discovered", got_frame)
            pump(app, publish_lag_s)
            published.append((ts + 25_000_000, value))
            published.sort()
            republish()
            return ts

        # ── 1. ONE shot. Its numbers must arrive on their own. ────────────────────
        ts1 = fire_shot(31.0, PUBLISH_LAG_S, "1")
        got = B.wait_for(
            lambda: v._pv_values.get("PTM1", "").startswith("31"), 15.0)
        check("this shot's own value arrives without a SECOND shot", got,
              f"values={v._pv_values} src={pv_src_ts()} want={ts1} "
              f"awaiting={v._pv_awaiting}")
        check("the numbers describe the frame on screen", pv_src_ts() == ts1,
              f"src={pv_src_ts()} want={ts1}")
        check("and they are shown unflagged",
              v._pv_display_text("PTM1").strip() == "31.000 J",
              f"showed {v._pv_display_text('PTM1')!r}")

        # ── 2. …and it was not a one-off: the next shot does the same. ────────────
        ts2 = fire_shot(32.0, PUBLISH_LAG_S, "2")
        got = B.wait_for(
            lambda: v._pv_values.get("PTM1", "").startswith("32"), 15.0)
        check("the next shot updates too", got,
              f"values={v._pv_values} src={pv_src_ts()} want={ts2}")
        check("still describing the frame on screen", pv_src_ts() == ts2,
              f"src={pv_src_ts()} want={ts2}")

        # ── 3. A sample published BEFORE the app even sees the frame. Nothing may
        #       be left waiting on a value that is already there. ─────────────────
        ts3 = fire_shot(33.0, 0.0, "3")
        got = B.wait_for(
            lambda: v._pv_values.get("PTM1", "").startswith("33"), 15.0)
        check("an already-published shot reads its own value at once", got,
              f"values={v._pv_values} src={pv_src_ts()} want={ts3}")
        check("no wait badge left", not v._pv_awaiting, f"awaiting={v._pv_awaiting}")
        check("'refresh behind' badge stays off", not v._pv_is_pending())

        # ── 4. The master tile really is showing that shot — i.e. the numbers and
        #       the picture describe the same moment. ───────────────────────────────
        shown = v._cam_shown_ts_ns[0] if v._cam_shown_ts_ns else 0
        check("the master tile is painted with the same frame", shown == ts3,
              f"painted={shown} pv={pv_src_ts()} want={ts3}")

        # ── 5. A 3.3 Hz BURST. The archiver cannot publish that fast, so the only
        #       question is whether OUR OWN requests queue up behind the shots. They
        #       must coalesce: the panel is single-flight with a dirty flag
        #       (_pv_trigger_fetch_now / _pv_on_result), so the number of round-trips
        #       is set by PV_REFRESH_MIN_INTERVAL_S and never by the frame rate. ─────
        print("\n[5] 3.3 Hz burst — the requests must coalesce, not accumulate")
        gen0 = v._pv_fetch_gen
        burst: "list[tuple[int, float]]" = []
        # The shot each displayed number was read for, sampled while the burst runs. A
        # backlog shows up here and nowhere else: a queue of stale results walks this
        # sequence BACKWARDS after the fact, and the operator sees numbers marching
        # through old shots long after they were fired.
        seen_src: "list[int]" = []
        t0 = time.monotonic()
        for k in range(20):
            ts = int(time.time() * 1e9)
            for ci, folder in enumerate(folders):
                write_frame(folder, cam_names[ci], ts)
            burst.append((ts, 40.0 + k))
            # ~1 s publication lag = three shots at this rate. Deliberately NO
            # republish() here: dropping the day cache 3x a second also drops the
            # published HEAD, and _pv_pick_fetch_ts needs that head to aim at an
            # already-published shot. The real archiver never does that — the cache
            # ages out on its own 0.5 s today-TTL, which is what this leaves it to.
            if k >= 3:
                published.append((burst[k - 3][0] + 25_000_000, burst[k - 3][1]))
                published.sort()
            end = time.monotonic() + 1.0 / 3.3
            while time.monotonic() < end:
                app.processEvents()
                src = pv_src_ts()
                if src and (not seen_src or seen_src[-1] != src):
                    seen_src.append(src)
                time.sleep(0.005)
        burst_s = time.monotonic() - t0
        fetches = v._pv_fetch_gen - gen0
        # +3 for the leading edge and the wait-retries; the point of the check is the
        # ORDER of magnitude — paced by the interval (~12 here) rather than one per
        # shot plus retries (~40+), which is what a queue would produce.
        cap = burst_s / m.PV_REFRESH_MIN_INTERVAL_S + 3
        check("fetches are paced by the refresh interval, not by the shot rate",
              fetches <= cap,
              f"{fetches} fetches / {len(burst)} shots in {burst_s:.1f} s "
              f"(cap {cap:.0f})")
        back = [i for i in range(1, len(seen_src)) if seen_src[i] < seen_src[i - 1]]
        check("the displayed shot never walks backwards (no queued backlog)",
              not back,
              f"{len(back)} rewind(s) over {len(seen_src)} distinct values")

        # ── 6. THE LASER STOPS. Nothing paints any more, so the wait-retry timer is
        #       the only thing left running — and it has to walk the panel onto the
        #       last frame's OWN numbers. This is the operator's acceptance test:
        #       "as soon as the laser stops, the PV updates to the last frame". ──────
        print("\n[6] the laser stops — the panel must land on the last shot")
        last_ts, last_val = burst[-1]
        for t, vv in burst[-3:]:
            published.append((t + 25_000_000, vv))
        published.sort()
        republish()
        got = B.wait_for(lambda: pv_src_ts() == last_ts, 25.0)
        check("the panel settles on the LAST frame's own value", got,
              f"src={pv_src_ts()} want={last_ts} values={v._pv_values} "
              f"awaiting={v._pv_awaiting}")
        check("that value is the last shot's", v._pv_values.get("PTM1", "").startswith(f"{last_val:.0f}"),
              f"values={v._pv_values} want {last_val}")
        check("and it is shown unflagged — no offset label",
              "(-" not in v._pv_display_text("PTM1"),
              f"showed {v._pv_display_text('PTM1')!r}")
        check("no wait badge left", not v._pv_awaiting, f"awaiting={v._pv_awaiting}")
        check("'refresh behind' badge stays off", not v._pv_is_pending())
    finally:
        try:
            v.close()
        except Exception:
            pass

    print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILURE(S):"))
    for f in FAILURES:
        print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
