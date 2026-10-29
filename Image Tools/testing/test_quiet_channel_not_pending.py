"""A quiet channel is not "waiting": no red dot for a setting nobody changed.

13.10.2026: the Image Slider showed red "waiting" dots on GDD / TOD
(L3-SPFE-AOD03-002:Order2_RB / Order3_RB) in the office. Those channels are
archived only when they change, and the setting classifier that answers them
needs several day reads; over the slow office link one of them timed out, the
classifier gave no verdict, and `lookup_near` fell into "today and the head is
before the frame → pending" — which is ALWAYS true for a setting. A look-back
that timed out was likewise turned into "pending".

Offline: the archiver is faked. Checks
  * a quiet channel the classifier could not decide on → "held", its last value;
  * a per-shot detector a second behind → still "pending" (its sample is on
    the way — the bug fixed in August must not come back);
  * a slow but steady cadence (25 s) → still "pending";
  * a failed look-back → "error", never "pending";
  * the Image Slider shows "held" as the frame's own reading (no dot: the
    archiver writes only on change), and long ages read in minutes / hours;
  * once the archiver's own clock is past the frame's window + margin, nothing
    pends any more and the last value before the frame is shown, no dot.

Run:  python "Image Tools/testing/test_quiet_channel_not_pending.py"
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import cpva_client as cpva      # noqa: E402

SEC = 1_000_000_000
fails: list[str] = []


def check(ok: bool, what: str, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {what}"
          f"{('  — ' + detail) if detail else ''}")
    if not ok:
        fails.append(what)


def fake(today_samples, verdict, back):
    """Fake one channel: today's samples, the classifier's answer and what the
    look-back finds."""
    ts_list = [t for t, _v in today_samples]
    cpva._lookup_narrow = lambda *a, **k: None
    cpva.get_day = lambda ch, dk, **k: cpva.DayResult(
        list(today_samples), "ok" if today_samples else "empty", 0.0,
        ts_list)
    cpva.classify_step_channel = lambda *a, **k: verdict
    cpva.is_step_channel = lambda *a, **k: False
    cpva.value_at_or_before = lambda *a, **k: back


def ask(frame_ns):
    return cpva.lookup_near("L3-TEST:Order2_RB", frame_ns, window_ns=SEC // 3,
                            pending_if_uncovered=True)


def main() -> int:
    today = cpva.today_key()
    day_start, _ = cpva.day_bounds_ns(today)
    frame = day_start + 12 * 3600 * SEC                       # noon today

    print("[1] a setting written three hours ago, classifier undecided")
    old = frame - 3 * 3600 * SEC
    fake([(old, 24300.0)], False,
         cpva.LookupResult(24300.0, old, "ok", True, old))
    res = ask(frame)
    check(res.status == "held", "held, not pending", res.status)
    check(res.value == 24300.0, "its last value", repr(res.value))
    check(res.ts_ns == old, "with the sample's own time")

    print("[2] nothing written today at all, last change yesterday")
    yesterday = day_start - 5 * 3600 * SEC
    fake([], False,
         cpva.LookupResult(-88000.0, yesterday, "ok", True, None))
    res = ask(frame)
    check(res.status == "held" and res.value == -88000.0,
          "held from yesterday", f"{res.status} {res.value}")

    print("[3] a per-shot detector one second behind the frame")
    shots = [(frame - SEC - i * 350_000_000, 0.02) for i in range(200)]
    fake(sorted(shots), False,
         cpva.LookupResult(0.02, shots[0][0], "ok", True, shots[0][0]))
    res = ask(frame)
    check(res.status == "pending", "still pending — its sample is on the way",
          res.status)

    print("[4] a steady shot every 25 s, the last one 24 s before the frame")
    slow = sorted((frame - 24 * SEC - i * 25 * SEC, 12.9) for i in range(40))
    fake(slow, False, cpva.LookupResult(12.9, slow[-1][0], "ok", True, None))
    res = ask(frame)
    check(res.status == "pending", "a slow cadence still pends", res.status)

    print("[5] the same detector silent for ten minutes (laser off)")
    off = sorted((frame - 600 * SEC - i * 5 * SEC, 12.9) for i in range(40))
    fake(off, False, cpva.LookupResult(12.9, off[-1][0], "ok", True, None))
    res = ask(frame)
    check(res.status == "held", "held, with its age", res.status)

    print("[6] a look-back that failed")
    fake([(old, 24300.0)], True,
         cpva.LookupResult(None, None, "error", True, None))
    res = ask(frame)
    check(res.status == "error", "an error, never pending", res.status)
    fake([(old, 24300.0)], False,
         cpva.LookupResult(None, None, "error", True, None))
    res = ask(frame)
    check(res.status == "error", "also on the quiet path", res.status)

    print("[7] the Image Slider's side")
    import is_t
    fake([(old, 24300.0)], False,
         cpva.LookupResult(24300.0, old, "ok", True, old))
    value, status, samp = is_t._pv_last_known_ex("L3-TEST:Order2_RB", frame)
    check((value, status, samp) == (24300.0, "ok", None),
          "held → the frame's own reading, no dot (written only on change)",
          f"{value} {status} {samp}")
    label = is_t.pv_offset_label(old, frame)
    check(label == " (-3.0 h)", "three hours reads as hours", repr(label))
    check(is_t.pv_offset_label(frame - 600 * SEC, frame) == " (-10 min)",
          "ten minutes reads as minutes")
    check(is_t.pv_offset_label(frame - 3 * SEC, frame) == " (-3.0 s)",
          "seconds unchanged")

    print("[8] written only on change: the archiver's clock decides, not the head")
    import time as _time

    def server_at(ns):
        # Fake the archiver's clock (normally from the HTTP Date header).
        cpva._server_offsets[:] = [ns / 1e9 - _time.time()]

    shots = sorted((frame - SEC - i * 350_000_000, 0.02) for i in range(200))
    fake(shots, False,
         cpva.LookupResult(0.0185, shots[-1][0], "ok", True, shots[-1][0]))
    server_at(frame + SEC // 2)
    res = ask(frame)
    check(res.status == "pending", "inside the publish margin → still pending",
          res.status)
    server_at(frame + 2 * SEC)
    res = ask(frame)
    check(res.status == "not_found", "past the margin → not pending", res.status)
    value, status, samp = is_t._pv_last_known_ex("L3-TEST:Order2_RB", frame)
    check((value, status, samp) == (0.0185, "ok", None),
          "→ last value before the frame, as its own reading (no dot)",
          f"{value} {status} {samp}")
    fake(shots, False, cpva.LookupResult(None, None, "error", True, None))
    value, status, _s = is_t._pv_last_known_ex("L3-TEST:Order2_RB", frame)
    check((value, status) == (None, "error"), "a failed look-back stays an error",
          f"{value} {status}")
    cpva._server_offsets.clear()

    print()
    print("all good" if not fails else f"{len(fails)} FAILED")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
