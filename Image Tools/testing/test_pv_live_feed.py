"""Live mode: the PV panel is a FEED; away from the live edge it is an exact pairing.

Decided with the user 13.10.2026. Following the live edge, the panel shows the
newest value the archiver holds, polled about once a second, NOT matched to the
frame on screen, and with no dot. The moment the view leaves the live edge
(navigation turns auto-follow off), every value is matched to its own frame again.

Checks, offscreen, with a faked archiver transport and a synthetic camera:
  * feed on → the newest archived value, no dot, frame changes look nothing up;
  * leaving the live edge → the exact value of the frame on screen;
  * back to live → the feed again;
  * a feed that stopped answering says so (grey dot).

    python testing/test_pv_live_feed.py
"""
import sys
import tempfile
import time
from pathlib import Path

import bench_common as B

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def main() -> int:
    m = B.load_slider()
    cpva = m.cpva
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    published: "list[tuple[int, float]]" = []
    fail = {"on": False}

    def fake_fetch_values_ex(channel, start_ns, end_ns, *, timeout=None,
                             try_value_suffix=True):
        if fail["on"]:
            raise cpva.CpvaError("timeout after 3.0s (faked)")
        return ([s for s in published if start_ns <= s[0] <= end_ns], channel)

    cpva.fetch_values_ex = fake_fetch_values_ex
    cpva.fetch_values = lambda ch, a, b, **k: fake_fetch_values_ex(ch, a, b)[0]
    cpva._server_offsets.clear()

    now = int(time.time() * 1e9)
    start_ns = now - 120_000_000_000
    tmp = Path(tempfile.mkdtemp(prefix="is_pvfeed_"))
    cam_names, cam_folder_lists = B.make_synthetic_set(
        tmp, 1, 4, hz=1.0 / 25.0, start_ns=start_ns)
    v = m.Viewer()
    try:
        v._diag_timer.stop()
    except Exception:
        pass
    v._pv_enabled = ["PTM1"]
    v._pv_hidden = set()
    v._ts_windows = None
    v._start_scan(list(cam_folder_lists[0]), axis_override=(start_ns, now),
                  folder_label=str(cam_folder_lists[0][0]))
    if not B.wait_for(lambda: bool(v.ts_list), 120.0):
        print("scan did not finish")
        return 1
    frames = list(v.ts_list)
    # Every frame has its own sample (value 10 + i); the newest archived value, a
    # second ago, is 99 — newer than any frame.
    for i, t in enumerate(frames):
        published.append((t + 25_000_000, 10.0 + i))
    published.append((now - 1_000_000_000, 99.0))
    published.sort()
    cpva.invalidate()
    cpva.invalidate_lookback()

    lookups = {"n": 0}
    real_lookup = m._pv_last_known_ex

    def counting_lookup(*a, **k):
        lookups["n"] += 1
        return real_lookup(*a, **k)
    m._pv_last_known_ex = counting_lookup

    print("\n[1] live edge: the feed")
    v._display_exact_index(1, frames[1], True)
    v._online_mode = True
    v._auto_follow = True
    v._pv_live_sync()
    got = B.wait_for(lambda: v._pv_values.get("PTM1", "").startswith("99"), 20.0)
    check("the newest archived value, not frame 1's own 11", got,
          f"values={v._pv_values}")
    check("no dot", v._pv_state("PTM1") == "", repr(v._pv_state("PTM1")))
    check("no '(old)' / offset label", v._pv_display_text("PTM1").strip() == "99.000 J",
          repr(v._pv_display_text("PTM1")))
    tip = v._pv_row_value("PTM1")[2]
    check("the tooltip says it is not matched to the picture",
          "NOT matched" in tip, repr(tip))
    lookups["n"] = 0
    v._display_exact_index(2, frames[2], True)
    v._pv_trigger_fetch()
    B.wait_for(lambda: False, 1.5)
    check("a frame change looks nothing up per frame", lookups["n"] == 0,
          f"{lookups['n']} lookup(s)")
    check("the feed keeps polling", v._pv_live_timer.isActive())

    print("\n[2] leaving the live edge: exact pairing")
    v._auto_follow = False
    v._online_mode = False
    v._pv_live_sync()
    got = B.wait_for(lambda: v._pv_values.get("PTM1", "").startswith("12"), 20.0)
    check("frame 2 reads its own value (12)", got, f"values={v._pv_values}")
    check("the feed stopped", not v._pv_live_timer.isActive())
    check("and its own value has no dot", v._pv_state("PTM1") == "",
          repr(v._pv_state("PTM1")))

    print("\n[3] back to live")
    v._online_mode = True
    v._auto_follow = True
    v._pv_live_sync()
    got = B.wait_for(lambda: v._pv_values.get("PTM1", "").startswith("99"), 20.0)
    check("the feed is back", got, f"values={v._pv_values}")

    print("\n[4] the archiver stops answering")
    fail["on"] = True
    v._pv_live_ok_mono = time.monotonic() - (m.PV_LIVE_STALE_S + 5)
    check("grey dot", v._pv_state("PTM1") == m.PV_STATE_NONE,
          repr(v._pv_state("PTM1")))
    check("and the tooltip says since when",
          "has not answered" in v._pv_row_value("PTM1")[2])
    fail["on"] = False
    v._online_mode = False
    v._auto_follow = False
    v._pv_live_sync()

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
