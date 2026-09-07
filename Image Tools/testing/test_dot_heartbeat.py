"""The live dot must never stand still while the program is healthy.

The bug this pins: an idle-but-healthy source used to paint ONE steady colour, so
after the last shot the dot beat about four times (CAM_DOT_FRESH_S / 0.6 s) and then
sat there — indistinguishable from an abandoned window. Every healthy state blinks
now; only the RATE differs.

Run:  python "Image Tools/testing/test_dot_heartbeat.py"
"""
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "is_t.py"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


def _consts():
    """Read the two constants out of the source without importing the whole tab."""
    txt = SRC.read_text(encoding="utf-8-sig")
    out = {}
    for key in ("CAM_DOT_FRESH_S", "DOT_SLOW_BLINK_TICKS"):
        m = re.search(rf"^{key}\s*=\s*([0-9.]+)", txt, re.MULTILINE)
        assert m, f"{key} not found in is_t.py"
        out[key] = float(m.group(1))
    return out


def _phases(n_ticks: int, slow_ticks: int):
    """Reproduce the phase arithmetic of _on_cam_dot_blink / _on_online_blink."""
    fast, slow = [], []
    for tick in range(1, n_ticks + 1):
        fast.append(bool(tick % 2))
        slow.append(bool((tick // slow_ticks) % 2))
    return fast, slow


def main():
    fails = []
    c = _consts()
    slow_ticks = int(c["DOT_SLOW_BLINK_TICKS"])

    # 1) The slow beat must actually beat, over a stretch far longer than the ~4
    #    blinks the old steady rule managed.
    ticks = 60                      # 36 s at the 600 ms timer
    fast, slow = _phases(ticks, slow_ticks)
    if len(set(slow)) != 2:
        fails.append("the idle (slow) phase never changes — the dot would stand still")
    if len(set(fast)) != 2:
        fails.append("the active (fast) phase never changes")

    # 2) The two rates must be tellable apart: slow changes strictly less often.
    def _changes(seq):
        return sum(1 for a, b in zip(seq, seq[1:]) if a != b)

    ch_fast, ch_slow = _changes(fast), _changes(slow)
    if not ch_slow < ch_fast:
        fails.append(f"slow beat is not slower than fast ({ch_slow} vs {ch_fast} changes)")
    if ch_fast != ticks - 1:
        fails.append(f"fast beat changed {ch_fast} times in {ticks} ticks, want every tick")

    # 3) The slow period is DOT_SLOW_BLINK_TICKS on, DOT_SLOW_BLINK_TICKS off.
    want_changes = (ticks - 1) // slow_ticks
    if abs(ch_slow - want_changes) > 1:
        fails.append(f"slow beat changed {ch_slow} times, want about {want_changes}")

    # 4) The dot must not go quiet before the old steady rule kicked in, either:
    #    inside CAM_DOT_FRESH_S there must already be several fast changes.
    beats_before_idle = int(c["CAM_DOT_FRESH_S"] / 0.6)
    if beats_before_idle < 4:
        fails.append("CAM_DOT_FRESH_S is now so short the active beat is invisible")

    # 5) The painter must give "idle" two colours, not one — this is the actual
    #    regression guard, read off the source.
    txt = SRC.read_text(encoding="utf-8-sig")
    m = re.search(r'elif state == "idle":(.*?)\n        else:', txt, re.DOTALL)
    if not m:
        fails.append("could not find the idle branch of pulse_refresh_dot")
    else:
        body = m.group(1)
        if body.count("if blink_on else") < 2:
            fails.append("pulse_refresh_dot paints idle with a steady colour again")

    if re.search(r'color = "#17801a"\s*\n\s*self\._online_dot\.setStyleSheet', txt):
        fails.append("_on_online_blink paints idle with a steady colour again")

    for f in fails:
        print("FAIL:", f)
    print("PASS" if not fails else f"{len(fails)} FAILURE(S)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
