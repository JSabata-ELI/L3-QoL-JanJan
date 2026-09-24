"""Frame choice for one cycle moment: never later than the moment, newest one
at or before it, and the skip rule for a frame that is too old.

Run:  python testing/test_frame_pick.py
"""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import s  # noqa: E402

SEC = 1_000_000_000
FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def make_dir(tmp: Path, ts_list) -> Path:
    cam = tmp / "L3-PAM12NF-C123"
    cam.mkdir(parents=True, exist_ok=True)
    for ts in ts_list:
        (cam / f"frame_{ts}.png").write_bytes(b"x")
    return cam


def test_at_or_before():
    print("at-or-before selection")
    base = 1_757_000_000 * SEC
    with tempfile.TemporaryDirectory() as td:
        cam = make_dir(Path(td), [base, base + 2 * SEC, base + 7 * SEC])

        src, age = s.find_frame_at_or_before(cam, base + 5 * SEC)
        check("picks the newest frame at or before the moment",
              src is not None and src.name == f"frame_{base + 2 * SEC}.png")
        check("reports how far behind it is", age == 3 * SEC)

        src, age = s.find_frame_at_or_before(cam, base + 7 * SEC)
        check("a frame exactly on the moment counts",
              src is not None and src.name == f"frame_{base + 7 * SEC}.png" and age == 0)

        src, age = s.find_frame_at_or_before(cam, base - SEC)
        check("nothing old enough -> no frame", src is None and age is None)

        # The bug this replaces: min(abs(...)) returned the 7 s LATER frame for
        # a moment at +3 s, because it is nearer in absolute terms.
        src, _ = s.find_frame_at_or_before(cam, base + 3 * SEC)
        check("never a frame from after the moment",
              src is not None and int(src.stem.split("_")[1]) <= base + 3 * SEC)


def test_cache():
    print("listing cache")
    base = 1_757_000_000 * SEC
    with tempfile.TemporaryDirectory() as td:
        cam = make_dir(Path(td), [base])
        cache, cache_time = {}, {}

        # Cold: scans and remembers.
        src, _ = s.find_frame_at_or_before(cam, base + SEC, None, cache, cache_time, 5.0)
        check("cold scan finds the frame", src is not None)
        check("listing was cached", str(cam) in cache)

        # A frame written after the scan: the cached listing ends before the
        # requested moment, so it cannot answer and must be re-read even though
        # the TTL still holds.
        (cam / f"frame_{base + 4 * SEC}.png").write_bytes(b"x")
        src, _ = s.find_frame_at_or_before(cam, base + 5 * SEC, None, cache, cache_time, 5.0)
        check("a listing that stops before the moment is re-read",
              src is not None and src.name == f"frame_{base + 4 * SEC}.png")

        # A cached listing that already reaches past the moment is trusted.
        cache[str(cam)] = [(base + 9 * SEC, "planted.png")]
        cache_time[str(cam)] = time.time()
        src, _ = s.find_frame_at_or_before(cam, base + 9 * SEC, None, cache, cache_time, 5.0)
        check("a listing that reaches past the moment is used as is",
              src is not None and src.name == "planted.png")


def test_skip_rule():
    print("skip rule")
    old = s.AUTO_BACK_WINDOW_NS + SEC
    a, b = Path("a.png"), Path("b.png")
    check("fresh frame is copied", not s.auto_should_skip(2 * SEC, a, None))
    check("frame exactly at the window edge is copied",
          not s.auto_should_skip(s.AUTO_BACK_WINDOW_NS, a, None))
    check("old frame the previous cycle also used is copied",
          not s.auto_should_skip(old, a, a))
    check("old frame different from the previous cycle is skipped",
          s.auto_should_skip(old, a, b))
    check("old frame with no previous cycle is skipped",
          s.auto_should_skip(old, a, None))
    check("no frame at all is skipped", s.auto_should_skip(None, None, a))


if __name__ == "__main__":
    test_at_or_before()
    test_cache()
    test_skip_rule()
    print()
    if FAILED:
        print(f"{len(FAILED)} FAILED: {FAILED}")
        sys.exit(1)
    print("all ok")
