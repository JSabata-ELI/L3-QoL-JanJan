"""Assert the PV panel WAITS for the archiver instead of trailing the picture by a shot.

The reported bug: shoot, the image updates to the new frame, and the PV/energy values
update to the values of the PREVIOUS image. Cause, measured on 2026-08-14 against the
archiver server's own clock (the local workstation clock is ~25 s ahead, which is why a
naive local-clock measurement reads a 25 s "archiver lag" that does not exist):

  * an image is on the share ~0.02 s after its own timestamp,
  * its sample becomes READABLE through the CPVA API ~0.2-2.5 s later (p50 0.9 s),
  * the panel fetched on the LEADING edge of the frame change, i.e. a few ms after the
    image appeared, asked for a +-0.3 s window that the sample was not in yet, kept the
    last good number — and nothing ever asked again, because only a frame change
    triggered a fetch. With one frame per shot the panel therefore stayed one shot
    behind for as long as the operator kept shooting.

Case 2 below is that bug: the values for the displayed frame must arrive WITHOUT the
operator touching anything. Runs offscreen against a fake archiver transport and local
files — no share, no network:

  python bench_pv_wait.py
"""
import os
import shutil
import tempfile
import time
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


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

    # ── a fake archiver whose PUBLISHED head we control ───────────────────────────
    # The transport is faked, not the lookup: get_day, the incremental tail merge, the
    # +-window match and the pending/not-found decision are all the shipping code.
    published: "list[tuple[int, float]]" = []

    def fake_fetch_values_ex(channel, start_ns, end_ns, *, timeout=None,
                             try_value_suffix=True):
        return ([s for s in published if start_ns <= s[0] <= end_ns], channel)

    cpva.fetch_values_ex = fake_fetch_values_ex
    cpva.fetch_values = lambda ch, a, b, **k: fake_fetch_values_ex(ch, a, b)[0]

    def republish():
        """Drop every cache so the next lookup sees the current `published` list."""
        cpva.invalidate()
        cpva.invalidate_lookback()

    # Two cadences in one timeline, both stamped TODAY (only today can pend, and today is
    # decided by the local clock — skewed or not, it is the clock the app runs on):
    #   * a 3.3 Hz burst first: faster than the archiver publishes, the case where the
    #     panel is allowed to skip a shot and show the last published one instead;
    #   * then three single shots 25 s apart: the cadence the bug was reported at.
    step_ns = 25_000_000_000
    fast_step = 300_000_000
    newest_ns = int(time.time() * 1e9) - 2_000_000_000
    start_ns = newest_ns - 2 * step_ns
    fast_ts = [start_ns - 10_000_000_000 + i * fast_step for i in range(6)]

    tmp = Path(tempfile.mkdtemp(prefix="is_pvwait_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(
        tmp, 1, 3, hz=1.0 / 25.0, start_ns=start_ns)
    for t in fast_ts:
        write_frame(cam_folder_lists[0][0], cam_names[0], t)
    frame_ts = sorted(t for p in cam_folder_lists[0][0].glob("*.png")
                      if (t := m.parse_unix_ns_from_name(p)))
    frame_ts = [t for t in frame_ts if t not in set(fast_ts)]
    print("slow shots:",
          [time.strftime('%H:%M:%S', time.localtime(t / 1e9)) for t in frame_ts])
    print("fast burst:",
          [time.strftime('%H:%M:%S', time.localtime(t / 1e9)) for t in fast_ts])

    CH = m.pv_channel_for("PTM1")
    # A shot's sample is TIMESTAMPED next to its image (measured p50 0.025 s, p90 0.3 s —
    # the reason the match window is +-0.3 s) but becomes READABLE about a second later.
    # Those are two different delays and only the second one is the bug: the sample below
    # is stamped 25 ms after its frame, and appears in `published` when we say so.
    def sample_for(i: int) -> "tuple[int, float]":
        return (frame_ts[i] + 25_000_000, 10.0 + i)

    # Shots 0 and 1 are published; the newest one is not — exactly the state the panel
    # is in a few milliseconds after a shot lands on the share.
    published += [sample_for(0), sample_for(1)]
    republish()

    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()

    try:
        axis = (fast_ts[0], frame_ts[-1])
        v._ts_windows = None
        v._start_scan(list(cam_folder_lists[0]), axis_override=axis,
                      folder_label=str(cam_folder_lists[0][0]))
        if not B.wait_for(lambda: bool(v.ts_list), 120.0):
            print("scan did not finish")
            return 1
        idx_of = {t: i for i, t in enumerate(v.ts_list)}
        check("all frames scanned", len(v.ts_list) == len(frame_ts) + len(fast_ts),
              f"{len(v.ts_list)} frames")

        # ── 0. the previous shot, read normally: this is the number that then gets
        #       held over and mistaken for the next frame's.
        print("\n[0] the previous shot, whose sample IS published")
        i1 = idx_of[frame_ts[1]]
        v._display_exact_index(i1, v.items[i1].ts_ns, True)
        got_prev = B.wait_for(
            lambda: v._pv_values.get("PTM1", "").startswith("11"), 20.0)
        check("published frame reads its own value", got_prev,
              f"values={v._pv_values}")
        check("and reads it unflagged", v._pv_display_text("PTM1").strip() == "11.000 J",
              f"showed {v._pv_display_text('PTM1')!r}")

        # Land on the newest frame, the way live auto-follow does.
        v._display_exact_index(len(v.items) - 1, v.items[-1].ts_ns, True)
        check("displaying the newest frame", v._pv_current_ts() == frame_ts[-1],
              f"{v._pv_current_ts()} != {frame_ts[-1]}")

        # ── 1. THE UNPUBLISHED FRAME MUST NOT LOOK ANSWERED ──────────────────────
        print("\n[1] the newest shot is not published yet")
        B.wait_for(lambda: not v._pv_fetch_inflight and bool(v._pv_values), 20.0)
        B.wait_for(lambda: bool(v._pv_awaiting), 5.0)
        check("panel reports it is waiting", "PTM1" in v._pv_awaiting,
              f"awaiting={v._pv_awaiting} values={v._pv_values}")
        txt = v._pv_display_text("PTM1")
        # The previous shot's number is 11.0. Showing it unflagged next to this frame is
        # precisely the reported bug.
        check("previous shot's number is not presented as this frame's",
              txt.strip() not in ("11.000 J", "11.000"), f"showed {txt!r}")
        check("held value says how far back it comes from",
              "-25 s" in txt or cpva.PV_TEXT_PENDING in txt, f"showed {txt!r}")

        # ── 2. THE FIX: the value arrives on its own once the archiver publishes ──
        print("\n[2] the archiver publishes it — no user interaction from here on")
        published.append(sample_for(2))
        published.sort()
        arrived = B.wait_for(
            lambda: (not v._pv_awaiting) and v._pv_values.get("PTM1", "").startswith("12"),
            15.0)
        check("this frame's own value arrives without the operator moving", arrived,
              f"awaiting={v._pv_awaiting} values={v._pv_values}")
        txt = v._pv_display_text("PTM1")
        check("and it is shown unflagged", txt.strip() == "12.000 J", f"showed {txt!r}")
        check("no wait badge left", not any(n in v._pv_awaiting
                                           for n in v._pv_visible_names()))

        # ── 3. GIVE UP eventually, instead of polling the archiver forever ───────
        print("\n[3] a frame that will never have a sample stops the waiting")
        m.PV_ARCHIVER_MAX_WAIT_S = 2.0
        published[:] = [s for s in published if s[0] < frame_ts[-1]]
        republish()
        v._pv_last_good = {}          # nothing to hold → the honest token must show
        v._pv_last_good_ts = {}
        v._pv_force_refresh()
        B.wait_for(lambda: bool(v._pv_awaiting), 10.0)
        check("waiting again after the sample disappeared", bool(v._pv_awaiting),
              f"awaiting={v._pv_awaiting} values={v._pv_values}")
        gave_up = B.wait_for(lambda: not v._pv_awaiting, 12.0)
        check("gives up after PV_ARCHIVER_MAX_WAIT_S", gave_up,
              f"awaiting={v._pv_awaiting}")
        t = getattr(v, "_pv_wait_timer", None)
        check("retry timer is stopped", t is None or not t.isActive())
        check("value reads n/a, not a stuck 'wait'",
              v._pv_values.get("PTM1") == cpva.PV_TEXT_NOT_FOUND,
              f"values={v._pv_values}")

        # ── 3b. FAST SHOTS: show the last PUBLISHED shot, labelled, not "wait" ───
        # Above ~1 shot/s the newest frame is always younger than the publication delay,
        # so waiting for it would mean the panel never shows a number during a run. It
        # steps back to the newest frame the archiver HAS published instead — a real
        # per-shot reading — and labels the gap. The IMAGES still show every frame.
        print("\n[3b] fast shots: the last published shot, labelled")
        m.PV_ARCHIVER_MAX_WAIT_S = 20.0
        # Everything up to the third-from-last burst frame is published; the last two are
        # not — what a ~0.9 s publication delay looks like at 3.3 Hz.
        published[:] = [(t + 25_000_000, 20.0 + i) for i, t in enumerate(fast_ts[:-2])]
        republish()
        v._pv_last_good = {}
        v._pv_last_good_ts = {}
        i_last = idx_of[fast_ts[-1]]
        v._display_exact_index(i_last, v.items[i_last].ts_ns, True)
        # First fetch fills the day cache (the head is unknown until then), the retry
        # re-aims — so let the panel settle instead of asserting on the first result.
        settled = B.wait_for(
            lambda: v._pv_values.get("PTM1", "").startswith("2")
            and v._pv_last_good_ts.get("PTM1") == fast_ts[-3], 15.0)
        check("aims at the newest PUBLISHED frame", settled,
              f"values={v._pv_values} src={v._pv_last_good_ts.get('PTM1')} "
              f"want={fast_ts[-3]} awaiting={v._pv_awaiting}")
        check("shows that shot's real value",
              v._pv_values.get("PTM1") == "23.000", f"values={v._pv_values}")
        txt = v._pv_display_text("PTM1")
        check("labelled with the sub-second gap, not passed off as this frame's",
              txt.strip() == "23.000 J (-0.6 s)", f"showed {txt!r}")
        check("no 'wait' left — the number IS a real reading", not v._pv_awaiting,
              f"awaiting={v._pv_awaiting}")
        check("'refresh behind' badge stays off (the offset is deliberate)",
              not v._pv_is_pending())
        # …and the moment the archiver catches up, the panel snaps onto the frame the
        # operator is actually looking at, with no interaction.
        published.extend((t + 25_000_000, 20.0 + i)
                         for i, t in enumerate(fast_ts) if i >= len(fast_ts) - 2)
        published.sort()
        caught_up = B.wait_for(
            lambda: v._pv_last_good_ts.get("PTM1") == fast_ts[-1], 15.0)
        check("snaps onto the displayed frame once it is published", caught_up,
              f"src={v._pv_last_good_ts.get('PTM1')} want={fast_ts[-1]}")
        check("and drops the offset label",
              v._pv_display_text("PTM1").strip() == "25.000 J",
              f"showed {v._pv_display_text('PTM1')!r}")

        # ── 3c. ARCHIVE BROWSING: every shot resolves exactly ────────────────────
        print("\n[3c] browsing back: every shot keeps its own value")
        for k, want in ((0, "20.000"), (1, "21.000"), (3, "23.000")):
            idx = idx_of[fast_ts[k]]
            v._display_exact_index(idx, v.items[idx].ts_ns, True)
            ok = B.wait_for(lambda: v._pv_values.get("PTM1") == want, 15.0)
            check(f"frame {k} reads its own value ({want})", ok,
                  f"values={v._pv_values}")
            check(f"frame {k} is unflagged",
                  v._pv_display_text("PTM1").strip() == f"{want} J",
                  f"showed {v._pv_display_text('PTM1')!r}")

        # ── 4. cpva contract: pending is only for TODAY and only past the head ───
        print("\n[4] cpva.lookup_near pending contract")
        published[:] = [sample_for(0)]
        republish()
        r = cpva.lookup_near(CH, frame_ts[-1], window_ns=m._PV_WINDOW_NS,
                             prefer="nearest", pending_if_uncovered=True,
                             today_ttl=m._PV_TODAY_CACHE_TTL)
        check("unpublished moment → 'pending'", r.status == "pending", f"{r.status!r}")
        check("head is reported", r.head_ts_ns == published[-1][0], f"{r.head_ts_ns}")
        r = cpva.lookup_near(CH, frame_ts[-1], window_ns=m._PV_WINDOW_NS,
                             prefer="nearest", today_ttl=m._PV_TODAY_CACHE_TTL)
        check("without the flag the old contract is unchanged",
              r.status == "not_found", f"{r.status!r}")
        # Head PAST the asked moment → a real gap in the data, not a wait.
        published.append((frame_ts[-1] + 30_000_000_000, 13.0))
        republish()
        r = cpva.lookup_near(CH, frame_ts[-1], window_ns=m._PV_WINDOW_NS,
                             prefer="nearest", pending_if_uncovered=True,
                             today_ttl=m._PV_TODAY_CACHE_TTL)
        check("covered moment with no sample → 'not_found'", r.status == "not_found",
              f"{r.status!r}")
        # A finished day can never grow, so it must never pend.
        past = frame_ts[0] - 3 * 86_400 * 1_000_000_000
        republish()
        r = cpva.lookup_near(CH, past, window_ns=m._PV_WINDOW_NS, prefer="nearest",
                             pending_if_uncovered=True,
                             today_ttl=m._PV_TODAY_CACHE_TTL)
        check("a past day never pends", r.status == "not_found", f"{r.status!r}")
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
