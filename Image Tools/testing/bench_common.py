"""Shared scaffolding for bench_drag.py / bench_play.py.

These harnesses drive the REAL widgets — the same signals a mouse would emit — so what
they measure is the shipping code path, not a re-implementation of it that drifts. The
numbers come out of the paint ledger inside the app (Viewer._bench, enabled by the
IMAGE_TOOLS_BENCH environment variable), which is appended to by _cam_note_painted: the
one place every path that puts a pixmap on a tile has to go through.

The metric that matters is |painted_ts - requested_ts|. It is what the whole preview layer
exists to keep small, and nothing recorded it before — a drag that painted frames minutes
away from the slider was indistinguishable in the logs from one that tracked perfectly.

Usage: import this before anything Qt, so the offscreen platform is set first.
"""
import os
import sys

# MUST precede any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["IMAGE_TOOLS_BENCH"] = "1"

import importlib.util
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent   # app dir; the harnesses sit in testing/

# Paint kinds recorded by _cam_note_painted.
KIND_EXACT, KIND_APPROX, KIND_STALE = 0, 1, 2


def load_slider():
    """Load is_t.py as the `image_slider` module, the way main.py does."""
    if "image_slider" in sys.modules:
        return sys.modules["image_slider"]
    argv, sys.argv = sys.argv, ["is_t.py"]
    try:
        spec = importlib.util.spec_from_file_location(
            "image_slider", str(HERE / "is_t.py"))
        m = importlib.util.module_from_spec(spec)
        sys.modules["image_slider"] = m
        spec.loader.exec_module(m)
        return m
    finally:
        sys.argv = argv


def make_synthetic_set(root: Path, cams: int, frames: int, hz: float = 3.3,
                       w: int = 445, h: int = 420, start_ns: int = 1_760_000_000_000_000_000):
    """Write `cams` folders of `frames` 16-bit PNGs named with unix-ns timestamps.

    Real data lives on a slow SMB share, which is exactly what makes it useful to measure
    and impossible to use in a fast repeatable test. Local files are far quicker per read,
    so absolute frames/s from a synthetic run is optimistic — what it does measure honestly
    is the SCHEDULING: whether every camera advances together, whether completed decodes
    reach the screen, and how far the painted frame is from the requested one.
    """
    from PySide6.QtGui import QImage
    import numpy as np

    step_ns = int(1e9 / hz)
    cam_names, cam_folder_lists = [], []
    for c in range(cams):
        name = f"BENCHCAM{c}"
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        cam_names.append(name)
        cam_folder_lists.append([folder])
        existing = len(list(folder.glob("*.png")))
        if existing >= frames:
            continue
        for i in range(frames):
            ts = start_ns + i * step_ns + c * (step_ns // max(1, cams))
            p = folder / f"{name}_{ts:019d}.png"
            if p.exists():
                continue
            # A gradient that moves with the frame index, in the bottom few percent of the
            # 16-bit range — the same region the real cameras occupy, which is what makes
            # the auto-stretch path meaningful.
            arr = np.zeros((h, w), dtype=np.uint16)
            arr[:, :] = np.linspace(1400, 3060, w, dtype=np.uint16)
            band = (i * 7) % h
            arr[band:band + 12, :] = 60000
            img = QImage(arr.tobytes(), w, h, w * 2, QImage.Format.Format_Grayscale16)
            img.save(str(p), "PNG")
    return cam_names, cam_folder_lists


def install_read_latency(m, ms: float):
    """Make every frame read cost `ms`, the way the real share does.

    Without this the benchmark is meaningless for the problem it exists to measure. A local
    PNG read is ~1 ms; a read off \\\\users-L3 is 130-160 ms (see the _open_reader
    docstring). At 1 ms, one-read-at-a-time per camera still yields ~1000 frames/s, so the
    in-flight cap that actually limited every tile to ~7 frames/s is invisible locally.

    time.sleep releases the GIL, exactly as the socket read it stands in for does, so the
    concurrency being measured is real: N threads sleeping 145 ms complete N reads per
    145 ms, and a serialised pipeline completes one.
    """
    if ms <= 0:
        return
    import time as _t
    orig = m._open_reader
    delay = ms / 1000.0

    def slow_open_reader(path):
        _t.sleep(delay)
        return orig(path)

    m._open_reader = slow_open_reader


def wait_for(pred, timeout_s=120.0, tick_ms=20):
    """Spin the Qt event loop until pred() or the timeout. Returns pred()'s truth."""
    import time
    from PySide6.QtCore import QCoreApplication
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        QCoreApplication.processEvents()
        if pred():
            return True
        time.sleep(tick_ms / 1000.0)
    return bool(pred())


def proxy_fraction(v) -> float:
    tracks = [t for t in (v._proxy_tracks or []) if t.planned]
    if not tracks:
        return 0.0
    planned = sum(len(t.planned) for t in tracks)
    return (sum(len(t.frames) for t in tracks) / planned) if planned else 0.0


def note_targets(v, store):
    """Record what the sliders ASKED each camera for, so `shown` can be divided by
    `asked`. The ledger only sees paints; the user's goal is stated the other way round —
    "everything I drag across should appear" — and that needs the denominator."""
    for i, ts in enumerate(v._cam_target_ts_ns or []):
        if ts:
            store.setdefault(i, set()).add(ts)


def report(v, cam_names, elapsed_s, title, asked=None):
    """Turn the paint ledger into the per-camera table."""
    rows = list(v._bench or [])
    print(f"\n=== {title} ===")
    print(f"wall {elapsed_s:.2f} s   preview {proxy_fraction(v)*100:.0f} % decoded   "
          f"paints {len(rows)}")
    if not rows:
        print("  NO PAINTS RECORDED")
        return {}

    misses = getattr(v, "_diag_miss", 0)
    print(f"{'camera':<14}{'paints/s':>9}{'shown/asked':>13}{'exact':>7}{'~appr':>7}"
          f"{'stale':>7}{'|dt| p50':>10}{'p95':>9}{'max':>9}")
    out = {}
    for ci, name in enumerate(cam_names):
        mine = [r for r in rows if r[1] == ci]
        if not mine:
            print(f"{name:<14}{0:>9}{'-':>13}{'-':>7}{'-':>7}{'-':>7}"
                  f"{'-':>10}{'-':>9}{'-':>9}")
            out[name] = {"paints_per_s": 0.0, "shown_frac": 0.0}
            continue
        d = sorted(abs(r[2] - r[3]) / 1e9 for r in mine if r[2])
        kinds = [r[4] for r in mine]
        p50 = d[len(d) // 2] if d else 0.0
        p95 = d[int(len(d) * 0.95)] if d else 0.0
        rate = len(mine) / elapsed_s if elapsed_s else 0.0
        # The user's own definition of working: of the distinct moments the slider asked
        # this camera for, how many actually reached the screen?
        shown_frac, shown_txt = 0.0, "-"
        if asked and ci in asked and asked[ci]:
            painted = {r[3] for r in mine}
            hit = len(asked[ci] & painted)
            shown_frac = hit / len(asked[ci])
            shown_txt = f"{hit}/{len(asked[ci])}"
        out[name] = {
            "paints_per_s": rate,
            "shown_frac": shown_frac,
            "exact": kinds.count(KIND_EXACT),
            "approx": kinds.count(KIND_APPROX),
            "stale": kinds.count(KIND_STALE),
            "dt_p50_s": p50, "dt_p95_s": p95, "dt_max_s": (d[-1] if d else 0.0),
        }
        print(f"{name:<14}{rate:>9.1f}{shown_txt:>13}{kinds.count(KIND_EXACT):>7}"
              f"{kinds.count(KIND_APPROX):>7}{kinds.count(KIND_STALE):>7}"
              f"{p50:>10.3f}{p95:>9.3f}{(d[-1] if d else 0):>9.3f}")

    # Fairness across cameras is the actual complaint: "one camera changes fast, then the
    # next". A single slowest/fastest ratio says more than any per-camera average.
    rates = [o["paints_per_s"] for o in out.values()]
    if rates and max(rates) > 0:
        print(f"\nper-camera rate  min {min(rates):.1f}/s  max {max(rates):.1f}/s  "
              f"spread {min(rates)/max(rates)*100:.0f} % "
              f"(100 % = all cameras advancing together)")
    print(f"refusals (_diag_miss) {misses}   "
          f"preview paints {getattr(v, '_diag_prev', 0)}   "
          f"cache {getattr(v, '_diag_cach', 0)}   share loads {getattr(v, '_diag_load', 0)}")
    return out
