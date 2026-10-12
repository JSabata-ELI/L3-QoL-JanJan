"""Pointing Analysis — is the beam position in pixels right? (01.10.2026)

Synthetic Gaussian spots are written to PNG at known sub-pixel positions and fed
through the real per-frame routine (PointingAnalysisTask._process_one). The answer
must be the spot's offset from the sensor centre in ORIGINAL sensor pixels, where
the centre of a W-wide sensor is at pixel (W - 1) / 2.

Checked for 8-bit and 16-bit frames, for a sensor small enough to be measured at
full size and for one large enough to be shrunk to 512 px first.

Also checked: the fixed µrad chain (4.5 µm pixel, M = 1/66, f = 300 mm, Y up)
turns one pixel into 0.227 µrad.

    PYTHONIOENCODING=utf-8 python testing/test_pointing_centroid.py

Nothing is shown; no Qt window is built.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="eli_is_pointing_"))
os.environ["APPDATA"] = str(_TMP)

import numpy as np                                                    # noqa: E402
from PIL import Image                                                 # noqa: E402

import is_t                                                           # noqa: E402

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


class _Item:
    def __init__(self, path, ts_ns=1):
        self.path = Path(path)
        self.ts_ns = ts_ns


def spot(w, h, x, y, sigma, peak, bg):
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float64)
    return bg + peak * np.exp(-((xs - x) ** 2 + (ys - y) ** 2) / (2 * sigma ** 2))


def write(arr, bits, name):
    p = _TMP / name
    if bits == 16:
        Image.fromarray(np.clip(arr, 0, 65535).astype(np.uint16)).save(p)
    else:
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(p)
    return p


def measure(path, thr):
    r = is_t.PointingAnalysisTask._process_one(_Item(path), thr, 1.0)
    return None if r is None else (r[1], r[2])


def main() -> int:
    cases = [
        # (w, h, x, y, sigma, bits, tolerance px)
        (400, 300, 230.3, 120.7, 6.0, 8, 0.15),
        (400, 300, 199.5, 149.5, 6.0, 16, 0.15),   # exactly the centre → (0, 0)
        # A spot only 4 px wide: the hard threshold cut alone moves it up to ~0.2 px
        # (the cut edge falls between pixels) — a property of thresholding.
        (400, 300, 80.0, 250.25, 4.0, 16, 0.2),
        (2048, 1536, 1400.4, 600.6, 24.0, 16, 0.6),  # shrunk to 512 px first
        (2048, 1536, 1023.5, 767.5, 24.0, 8, 0.6),   # centre of a big sensor
        (1920, 1200, 300.2, 900.8, 20.0, 16, 0.6),
    ]
    for w, h, x, y, sg, bits, tol in cases:
        peak = 50000 if bits == 16 else 200
        bg = 800 if bits == 16 else 10
        p = write(spot(w, h, x, y, sg, peak, bg), bits, f"s_{w}_{x}_{y}_{bits}.png")
        want = (x - (w - 1) / 2.0, y - (h - 1) / 2.0)
        for thr in (0, 50, 90):
            got = measure(p, thr)
            ok = got is not None and abs(got[0] - want[0]) <= tol \
                and abs(got[1] - want[1]) <= tol
            check(f"{w}x{h} {bits}-bit spot at ({x}, {y}), threshold {thr} %",
                  ok, f"want ({want[0]:.2f}, {want[1]:.2f}) got "
                      + ("None" if got is None else f"({got[0]:.2f}, {got[1]:.2f})"))

    # A move of exactly one pixel must read as one pixel, also on a shrunk frame.
    for w, h, sg in ((400, 300, 6.0), (2048, 1536, 24.0)):
        a = measure(write(spot(w, h, w * 0.6, h * 0.4, sg, 50000, 500), 16, f"m0_{w}.png"), 50)
        b = measure(write(spot(w, h, w * 0.6 + 1, h * 0.4 + 1, sg, 50000, 500), 16,
                          f"m1_{w}.png"), 50)
        d = (b[0] - a[0], b[1] - a[1])
        check(f"{w}x{h}: a 1 px move reads as 1 px", abs(d[0] - 1) < 0.1 and abs(d[1] - 1) < 0.1,
              f"({d[0]:.3f}, {d[1]:.3f})")

    # ── 05.10.2026: noise, hot pixels, empty frames, saturation ──────────────
    rng = np.random.default_rng(7)

    def noisy(w, h, x, y, sg, peak, bg, sd, bits):
        a = bg + rng.normal(0, sd, (h, w))
        if peak:
            a = a + spot(w, h, x, y, sg, peak, 0)
        return a

    # Empty frames (background + noise only) give no point — 8- and 16-bit, small
    # and shrunk.
    for w, h, bits, bg, sd in ((400, 300, 8, 12, 2.0), (2048, 1536, 16, 800, 40.0)):
        p = write(noisy(w, h, 0, 0, 1, 0, bg, sd, bits), bits, f"empty_{w}_{bits}.png")
        r = measure(p, 90)
        check(f"{w}x{h} {bits}-bit empty frame gives no point", r is None,
              "" if r is None else f"got {r}")

    # A hot pixel on an empty frame is no beam either.
    a = noisy(400, 300, 0, 0, 1, 0, 12, 2.0, 8)
    a[40, 50] = 255
    r = measure(write(a, 8, "empty_hot.png"), 90)
    check("empty frame with one hot pixel gives no point", r is None,
          "" if r is None else f"got {r}")

    # A hot pixel brighter than the beam must not pull the beam position.
    for w, h, bits, peak, bg, sd, hot in ((400, 300, 8, 120, 12, 2.0, 255),
                                          (2048, 1536, 16, 20000, 800, 40.0, 65535)):
        x, y = w * 0.55 + 0.3, h * 0.45 - 0.2
        a = noisy(w, h, x, y, 6.0 if w < 1000 else 24.0, peak, bg, sd, bits)
        a[int(h * 0.1), int(w * 0.9)] = hot
        a[int(h * 0.8), int(w * 0.2)] = hot
        want = (x - (w - 1) / 2.0, y - (h - 1) / 2.0)
        # Threshold 0 included: on a noisy frame the clipped noise used to pull the
        # answer tens of pixels towards the middle of the frame.
        for thr in (0, 50, 90):
            got = measure(write(a, bits, f"hot_{w}_{thr}.png"), thr)
            ok = got is not None and abs(got[0] - want[0]) <= 0.3 \
                and abs(got[1] - want[1]) <= 0.3
            check(f"{w}x{h} {bits}-bit beam + hot pixels, threshold {thr} %", ok,
                  f"want ({want[0]:.2f}, {want[1]:.2f}) got "
                  + ("None" if got is None else f"({got[0]:.2f}, {got[1]:.2f})"))

    # Thresholding no longer moves a clean spot: under 0.05 px at every threshold.
    worst = 0.0
    for x, y in ((230.3, 120.7), (80.0, 250.25), (151.6, 77.45)):
        p = write(spot(400, 300, x, y, 4.0, 50000, 800), 16, f"bias_{x}.png")
        for thr in (30, 50, 70, 90):
            got = measure(p, thr)
            want = (x - 199.5, y - 149.5)
            worst = max(worst, abs(got[0] - want[0]), abs(got[1] - want[1]))
    check("threshold bias on a 4 px spot under 0.05 px", worst < 0.05, f"worst {worst:.3f} px")

    # Saturation is flagged.
    a = np.minimum(spot(400, 300, 200.0, 150.0, 6.0, 400, 10), 255)
    r = is_t.PointingAnalysisTask._process_one(_Item(write(a, 8, "sat.png")), 90, 1.0)
    check("a clipped beam is flagged saturated", r is not None and bool(r[6]))
    r = is_t.PointingAnalysisTask._process_one(
        _Item(write(spot(400, 300, 200.0, 150.0, 6.0, 200, 10), 8, "unsat.png")), 90, 1.0)
    check("a clean beam is not flagged saturated", r is not None and not r[6])

    # The run: empty frames first, then a faint glow, then the shots. Zero must be
    # the first real shot.
    frames = []
    for k in range(3):
        frames.append(write(noisy(400, 300, 0, 0, 1, 0, 12, 2.0, 8), 8, f"run_e{k}.png"))
    # A broad glow at 8 % of the beam's height — above the noise, so only the
    # run-level rule (pointing_drop_glow, 10 %) can tell it is no shot.
    frames.append(write(noisy(400, 300, 100, 100, 30.0, 16, 12, 2.0, 8), 8, "run_glow.png"))
    shots = [(200.0 + k, 150.0 - k) for k in range(5)]
    for k, (x, y) in enumerate(shots):
        frames.append(write(noisy(400, 300, x, y, 6.0, 200, 12, 2.0, 8), 8, f"run_s{k}.png"))
    res = [is_t.PointingAnalysisTask._process_one(_Item(p, ts_ns=i + 1), 50, 1.0)
           for i, p in enumerate(frames)]
    res = [r for r in res if r is not None]
    kept, dropped = is_t.pointing_drop_glow(sorted(res, key=lambda r: r[0]))
    check("run: empty frames give no result, the glow is dropped, 5 shots stay",
          len(kept) == 5 and kept[0][0] == 5, f"{len(res)} results, kept {len(kept)}, "
          f"first ts {kept[0][0] if kept else None}")
    if len(kept) == 5:
        dx = kept[-1][1] - kept[0][1]
        dy = kept[-1][2] - kept[0][2]
        check("run: last shot reads (+4, -4) px from the first real shot",
              abs(dx - 4) < 0.15 and abs(dy + 4) < 0.15, f"({dx:.3f}, {dy:.3f})")

    f = is_t.POINTING_URAD_PER_PX
    check("µrad per sensor pixel = 4.5 µm / 300 mm × 1/66 = 0.227",
          abs(f - 0.22727) < 1e-4, f"{f:.5f}")
    ux, uy = is_t.pointing_px_to_urad(np.array([10.0]), np.array([10.0]))
    check("µrad: X keeps its sign, Y is turned upwards",
          ux[0] > 0 and uy[0] < 0, f"({ux[0]:.3f}, {uy[0]:.3f})")

    print("\nALL PASS" if not FAILURES else f"\n{len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
