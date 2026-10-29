"""When a frame has no reading of its own, show the nearest one — and say how far.

Reported 17.09.2026: "often I got no data yet, and then n/a". Measured cause
(testing/probe_pv_match_window.py, probe_pv_sample_gaps.py): on 16.09.2026, 15:26-15:33,
the camera stored 1200 frames at 3.3 Hz while PTM1/SBW4/Back_Ref were archived once
every 5.0 s — so 93 % of frames had nothing inside the ±0.3 s pairing window and read
"n/a", with the only candidate reading sitting 2 s away.

The user's requirement, in their words: *"když přijde snímek, tak přijde i energie +-
nějaký čas... musíme ale myslet do budoucnosti a počítat s tím, že někdy budou přibývat
i energie rychlostí 3,3hz"* — show the value that came with the frame, but be ready for
the day the energies arrive as fast as the frames.

REVISED 13.10.2026: the archiver writes a sample only when the value CHANGES, so a
frame with nothing inside its window reads the last sample BEFORE it, as its own value
with no dot (permanent orange/red dots otherwise — the user's report). Both regimes:

  sparse (5 s)   → the last reading before the frame, plain, no offset.
  dense (3.3 Hz) → still no widening; a frame in a hole reads the last value before
                   the hole, plain.

  python testing/test_pv_na_reason.py
"""
import time

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def make_viewer(m, published):
    cpva = m.cpva
    cpva.fetch_values_ex = lambda ch, a, b, **k: (
        [s for s in published if a <= s[0] <= b], ch)
    cpva.fetch_values = lambda ch, a, b, **k: cpva.fetch_values_ex(ch, a, b)[0]
    cpva.invalidate()
    cpva.invalidate_lookback()
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()
    return v


def show_frame(app, v, ts_ns):
    v.items = [type("It", (), {"ts_ns": ts_ns})()]
    v.ts_list = [ts_ns]
    v.current_idx = 0
    v._pv_last_fetch_mono = 0.0
    v._pv_trigger_fetch_now()
    B.wait_for(lambda: not getattr(v, "_pv_fetch_inflight", False), 30.0)
    app.processEvents()
    return v._pv_row_value("PTM1")


def main() -> int:
    m = B.load_slider()
    cpva = m.cpva
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    base = int(time.time() * 1e9) - 3600 * 1_000_000_000      # an hour ago, a past day
    channel = m.pv_channel_for("PTM1")

    # ── 1. sparse: one reading every 5 s, the measured case ──────────────────────
    print("\n[1] archived every 5 s, camera at 3.3 Hz")
    step = 5_000_000_000
    published = [(base + i * step, 90.0 + i * 0.01) for i in range(200)]
    v = make_viewer(m, published)

    frame = published[40][0] + 2_500_000_000
    text, grey, tip = show_frame(app, v, frame)
    print(f"  row: {text!r} grey={grey}")
    for line in (tip or "(none)").splitlines():
        print(f"    {line}")
    # 13.10.2026: the archiver writes only on CHANGE, so nothing written since the
    # last sample means the value did not change — that sample IS this frame's value.
    check("the last reading BEFORE the frame is shown, not n/a",
          text.startswith("90.40"), f"text={text!r}")
    check("as this frame's own value — no dot", not grey, f"state={grey!r}")
    check("no offset is claimed", v._pv_sample_offset_s("PTM1") is None)

    burn = m.pv_text_for_ts(frame, ["PTM1"])
    print(f"  burn-in: {burn!r}")
    check("the burned-in line is plain too", m.pv_bar_plain(burn) == burn,
          f"burn={burn!r}")
    burn_on = m.pv_text_for_ts(published[41][0], ["PTM1"])
    check("and a frame with its own reading burns in plainly",
          m.pv_bar_plain(burn_on) == burn_on, f"burn={burn_on!r}")

    # A frame ON a reading keeps the plain number, no label, not greyed.
    text2, grey2, tip2 = show_frame(app, v, published[41][0])
    print(f"  on a reading: {text2!r} grey={grey2}")
    check("a frame that HAS its own reading is unlabelled",
          "s)" not in text2 and any(c.isdigit() for c in text2), f"text={text2!r}")
    check("and not greyed", not grey2, f"grey={grey2} tip={tip2!r}")

    # ── 2. dense: the future the user asked to be ready for ──────────────────────
    print("\n[2] archived at 3.3 Hz — the widening must switch itself off")
    dense_step = 300_000_000
    dense = [(base + i * dense_step, 90.0 + i * 0.001) for i in range(4000)]
    # …with a 3 s hole in the middle, the only way to be outside the window at all.
    hole_at = dense[2000][0]
    dense = [s for s in dense if not (hole_at < s[0] < hole_at + 3_000_000_000)]
    v2 = make_viewer(m, dense)

    check("no widening is applied to a 3.3 Hz channel",
          m._pv_wide_window_ns(channel, dense[2000][0]) == 0,
          f"{m._pv_wide_window_ns(channel, dense[2000][0]) / 1e9:.3f}s")
    text3, grey3, tip3 = show_frame(app, v2, hole_at + 1_500_000_000)
    print(f"  in the hole: {text3!r}")
    for line in (tip3 or "(none)").splitlines():
        print(f"    {line}")
    want = [s for s in dense if s[0] <= hole_at + 1_500_000_000][-1][1]
    check("a frame inside the hole reads the last value before it, no dot",
          text3.startswith(f"{want:.3f}") and not grey3,
          f"text={text3!r} want={want:.3f} state={grey3!r}")

    text4, _g4, _t4 = show_frame(app, v2, dense[1000][0])
    check("an ordinary 3.3 Hz frame still reads its own value plainly",
          "s)" not in text4 and any(c.isdigit() for c in text4), f"text={text4!r}")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
