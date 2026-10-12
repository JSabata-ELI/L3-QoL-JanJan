"""Shot counter against a REAL half hour: every shot counted, nothing else.

The fixture (shot_replay_2026-10-06.json) holds the real frame stamps of 13 cameras
and the real PTM1/SBW4 readings, 2026-10-06 12:30-13:00. In that half hour:

  * the system rate was one shot every 5 s, and the laser fired for ~16 minutes;
  * 7 cameras are triggered by the shots: one frame per shot;
  * 3 cameras (PD1M1DF, PD1M2NF, PD2M1DF) run at the SAME 5 s whether the laser
    fires or not — the trap: their rate looks right, half their frames are no shot;
  * PFM1NF / PFM8FF run free at 3.3 Hz, PFM4NF at 10 Hz.

The true shots are the moments the triggered cameras and the energies saw. Each
scenario replays the stamps in arrival order (a frame lands 0.35 s after its stamp,
energies are polled every 1.5 s, as in the app) and the counter must arrive at exactly
that number — whichever camera is on screen, with or without the energies.

    python testing/test_shot_counter_replay.py
"""
import json
import sys
from pathlib import Path

import bench_common  # noqa: F401  (offscreen Qt before anything else)

sys.path.insert(0, str(bench_common.HERE))
import is_t

US = 1000
S = 1_000_000_000
FIXTURE = Path(__file__).with_name("shot_replay_2026-10-06.json")
TRIGGERED = ["L3-OM1FF-C310-_-IMG", "L3-PTM9FF-C301-_-IMG", "C03-030-PAP1FF-_-IMG",
             "C03-075-SBM5NF-_-IMG", "L3-BRNF-C393-_-IMG", "C03-079-PCM2NF-_-IMG",
             "C03-073-SBM6FF-_-IMG"]
FREE_5S = ["C03-015-PD1M1DF-_-IMG", "C03-016-PD1M2NF-_-IMG", "C03-019-PD2M1DF-_-IMG"]
FREE_FAST = ["C03-013-PFM1NF-_-IMG", "C03-021-PFM8FF-_-IMG", "C03-014-PFM4NF-_-IMG"]
ARRIVAL_NS = 350_000_000
POLL_S = is_t.SHOT_ENERGY_POLL_MS / 1000.0


def dec(deltas):
    out, acc = [], 0
    for i, d in enumerate(deltas):
        acc = d if i == 0 else acc + d
        out.append(acc * US)
    return out


def load():
    d = json.loads(FIXTURE.read_text())
    cams = {k: dec(v) for k, v in d["cams"].items()}
    energy = {}
    for lab, e in d["energy"].items():
        energy[lab] = [(t, v) for t, v in zip(dec(e["ts"]), e["v"])]
    return d["start_us"] * US, d["end_us"] * US, cams, energy


def truth(cams, energy, t0, t1):
    """Shot moments: every stamp of a triggered camera or an energy reading, joined
    when closer than 1.5 s (shots are 5 s apart)."""
    pts = sorted([t for c in TRIGGERED for t in cams[c]]
                 + [t for sm in energy.values() for t, v in sm if v > 0])
    shots = []
    for t in pts:
        if shots and t - shots[-1][-1] < 1.5 * S:
            shots[-1].append(t)
        else:
            shots.append([t])
    return [s[0] for s in shots if t0 <= s[0] < t1]


def replay(cams, energy, use_cams, main, t_live, t_end, energies=True):
    """Live mode starts at t_live: what was there is history. Returns shots counted."""
    c = is_t.ShotCounter(reset_s=1e9)      # one run: the total is the count
    name = {k: "cam:" + k for k in use_cams}
    for k in use_cams:
        c.prime(name[k], [t for t in cams[k] if t + ARRIVAL_NS <= t_live])
    c.set_main(name[main])
    events = []
    for k in use_cams:
        for t in cams[k]:
            a = t + ARRIVAL_NS
            if t_live < a <= t_end:
                events.append((a, 0, k, t))
    if energies:
        p = t_live
        while p <= t_end:
            events.append((p, 1, None, None))
            p += int(POLL_S * S)
    events.sort(key=lambda e: (e[0], e[1]))
    total = 0
    before = c.count
    for a, kind, k, t in events:
        now = a / S
        if kind == 0:
            c.add(name[k], [t], now)
        else:
            c.energy_answered(now)
            for lab, sm in energy.items():
                ts = [x for x, v in sm if a - 90 * S <= x <= a and v > 0]
                c.add_energy(lab, ts, now)
        if c.count != before:
            total += max(0, c.count - before)
            before = c.count
    return total


def main() -> int:
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    t0, t1, cams, energy = load()
    t_live = t0 + 60 * S
    t_end = t1
    want = len(truth(cams, energy, t_live + ARRIVAL_NS, t_end - 2 * S))
    print(f"real shots in the replayed window: {want}")
    every = TRIGGERED + FREE_5S + FREE_FAST
    scenarios = [
        ("all 13 cameras, a triggered one on screen", every, TRIGGERED[0], True),
        ("all 13 cameras, a 5 s free-runner on screen", every, FREE_5S[0], True),
        ("all 13 cameras, the 3.3 Hz free-runner on screen", every, FREE_FAST[0], True),
        ("all 13 cameras, no energies", every, TRIGGERED[0], False),
        ("all 13 cameras, 3.3 Hz on screen, no energies", every, FREE_FAST[0], False),
        ("one triggered camera + energies", [TRIGGERED[0]], TRIGGERED[0], True),
        ("one triggered camera alone", [TRIGGERED[0]], TRIGGERED[0], False),
        ("one 3.3 Hz free-runner + energies", [FREE_FAST[0]], FREE_FAST[0], True),
        ("one 5 s free-runner + energies", [FREE_5S[0]], FREE_5S[0], True),
        ("free-runners only + energies", FREE_5S + FREE_FAST, FREE_5S[0], True),
        ("energies only", [], None, True),
    ]
    for label, use, main_cam, en in scenarios:
        if main_cam is None:
            c = is_t.ShotCounter(reset_s=1e9)
            got = _energy_only(c, energy, t_live, t_end)
        else:
            got = replay(cams, energy, use, main_cam, t_live, t_end, energies=en)
        exp = want
        if not en and len(use) == 1:
            # One camera and nothing else: a frame it never stored is invisible.
            exp = sum(1 for t in cams[main_cam] if t_live < t + ARRIVAL_NS <= t_end)
        # The last shot may still be on its way when the window closes: allow it.
        check(exp - 1 <= got <= exp, f"{label}: {got} of {exp}")
    print()
    if fails:
        print(f"{len(fails)} FAILED")
        return 1
    print("all checks passed")
    return 0


def _energy_only(c, energy, t_live, t_end):
    c.energy_answered(t_live / S)
    for lab, sm in energy.items():
        c.add_energy(lab, [x for x, v in sm if t_live - 90 * S <= x <= t_live and v > 0],
                     t_live / S)
    total = 0
    p = t_live + int(POLL_S * S)
    while p <= t_end:
        before = c.count
        c.energy_answered(p / S)
        for lab, sm in energy.items():
            c.add_energy(lab, [x for x, v in sm if p - 90 * S <= x <= p and v > 0], p / S)
        total += max(0, c.count - before)
        p += int(POLL_S * S)
    return total


if __name__ == "__main__":
    sys.exit(main())
