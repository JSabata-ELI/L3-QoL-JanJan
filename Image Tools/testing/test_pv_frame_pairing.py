"""A reading belongs to the frame it is NEAREST to — whatever each camera's rate is.

Measured 16.09.2026, 15:00-16:00, cameras all storing one frame every 5.00 s, against
PTM1 (testing/probe_cam_frame_offset.py, probe_cam_rates.py):

    PCM4NF   reading 0.164 s from the frame, spread 0.12 s → paired  92 %
    PCM2NF   reading 0.366 s from the frame, spread 0.11 s → paired   5 %
    PCW3NF   reading 0.512 s from the frame, spread 0.18 s → paired   0 %
    PTM9NF   reading 0.393 s from the frame, spread 0.32 s → paired  20 %

Every one of those cameras IS triggered by the shot; they only stamp their file with
their own small constant lag. The old fixed ±0.3 s ran straight through the middle of
that, so three of the four reported "no data" for readings that were plainly theirs —
the shot 5 s away was never a candidate.

The rule now: a reading may be claimed by a frame up to HALF the gap to that camera's
neighbouring frame (floor 0.3 s, cap 60 s), which cannot hand one reading to two
frames and needs no per-camera calibration. The user's requirement, in their words:
*"když přijde snímek, tak přijde i energie +- nějaký čas"*, and *"musíme počítat s tím,
že někdy budou přibývat i energie rychlostí 3,3hz"* — so it has to tighten by itself
when the frames come faster.

  python testing/test_pv_frame_pairing.py
"""
import time

import bench_common as B

FAILURES: "list[str]" = []

SEC = 1_000_000_000


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def run_case(m, app, *, frame_gap_s, cam_lag_s, label):
    """One camera: frames every `frame_gap_s`, each stamped `cam_lag_s` AFTER the
    shot whose energy was archived. Returns the text shown on a middle frame."""
    cpva = m.cpva
    base = int(time.time() * 1e9) - 7200 * SEC
    shots = [base + i * int(frame_gap_s * SEC) for i in range(200)]
    published = [(t, 90.0 + i * 0.01) for i, t in enumerate(shots)]
    cpva.fetch_values_ex = lambda ch, a, b, **k: (
        [s for s in published if a <= s[0] <= b], ch)
    cpva.fetch_values = lambda ch, a, b, **k: cpva.fetch_values_ex(ch, a, b)[0]
    cpva.invalidate()
    cpva.invalidate_lookback()

    frames = [t + int(cam_lag_s * SEC) for t in shots]
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()
    v.items = [type("It", (), {"ts_ns": t})() for t in frames]
    v.ts_list = list(frames)
    v.current_idx = 100
    v._pv_last_fetch_mono = 0.0
    v._pv_trigger_fetch_now()
    B.wait_for(lambda: not getattr(v, "_pv_fetch_inflight", False), 30.0)
    app.processEvents()
    text, grey, tip = v._pv_row_value("PTM1")
    want = f"{90.0 + 100 * 0.01:.3f}"
    print(f"\n[{label}] frames every {frame_gap_s:g} s, stamped {cam_lag_s:+g} s "
          f"off the shot")
    print(f"  claim window {v._pv_claim_window_ns() / 1e9:.2f} s   row {text!r} "
          f"grey={grey}")
    return v, text, grey, want


def main() -> int:
    m = B.load_slider()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    # ── the camera that already worked: 0.16 s off, inside the old window ───────
    v, text, grey, want = run_case(m, app, frame_gap_s=5.0, cam_lag_s=0.164,
                                   label="PCM4NF-like")
    check("a frame 0.16 s off its reading shows it plainly",
          text.startswith(want) and "s)" not in text, f"text={text!r} want~{want}")
    check("and is not greyed", not grey)

    # ── the camera that reported nothing: 0.37 s off, just outside ±0.3 s ───────
    v, text, grey, want = run_case(m, app, frame_gap_s=5.0, cam_lag_s=0.366,
                                   label="PCM2NF-like")
    check("a frame 0.37 s off its reading now shows it too",
          text.startswith(want), f"text={text!r} want~{want}")
    check("unlabelled — it IS this frame's shot, 5 s from any other",
          "s)" not in text, f"text={text!r}")
    check("and not greyed", not grey, f"grey={grey}")

    # ── the worst one: 0.51 s off ───────────────────────────────────────────────
    v, text, grey, want = run_case(m, app, frame_gap_s=5.0, cam_lag_s=0.512,
                                   label="PCW3NF-like")
    check("half a second off still pairs", text.startswith(want), f"text={text!r}")

    # ── and the future: frames at 3.3 Hz, one reading per frame ────────────────
    v, text, grey, want = run_case(m, app, frame_gap_s=0.30, cam_lag_s=0.02,
                                   label="3.3 Hz")
    check("at 3.3 Hz the claim window stays at the strict floor",
          abs(v._pv_claim_window_ns() - m._PV_WINDOW_NS) < 1000,
          f"{v._pv_claim_window_ns() / 1e9:.3f}s")
    check("and each frame reads its own shot", text.startswith(want),
          f"text={text!r} want~{want}")

    # ── a free-running camera at 3.3 Hz while the shots come every 5 s ─────────
    # The frames between shots must NOT claim a neighbour's reading as their own.
    cpva = m.cpva
    base = int(time.time() * 1e9) - 7200 * SEC
    published = [(base + i * 5 * SEC, 90.0 + i) for i in range(100)]
    cpva.fetch_values_ex = lambda ch, a, b, **k: (
        [s for s in published if a <= s[0] <= b], ch)
    cpva.fetch_values = lambda ch, a, b, **k: cpva.fetch_values_ex(ch, a, b)[0]
    cpva.invalidate()
    cpva.invalidate_lookback()
    frames = [base + int(i * 0.3 * SEC) for i in range(600)]
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()
    v.items = [type("It", (), {"ts_ns": t})() for t in frames]
    v.ts_list = list(frames)
    v.current_idx = 105              # 31.5 s in — 1.5 s after a shot
    v._pv_last_fetch_mono = 0.0
    v._pv_trigger_fetch_now()
    B.wait_for(lambda: not getattr(v, "_pv_fetch_inflight", False), 30.0)
    app.processEvents()
    text, grey, tip = v._pv_row_value("PTM1")
    print(f"\n[free-running] 3.3 Hz camera, shots every 5 s — frame between shots")
    print(f"  claim window {v._pv_claim_window_ns() / 1e9:.2f} s   row {text!r}")
    check("a frame between shots never shows a reading as its OWN",
          "s)" in text or text.startswith(m.cpva.PV_TEXT_NOT_FOUND), f"text={text!r}")
    check("and it is greyed either way", grey, f"grey={grey}")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
