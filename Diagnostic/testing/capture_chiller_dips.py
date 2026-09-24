"""Capture the 2026-09-23 Utility Chiller dips as an offline test fixture.

Run once, against the live archiver. Everything downstream reads the JSON, so
the tests need no network and cannot drift when the archive is trimmed.

The fixture is only worth keeping if it actually holds the thing under test:
three single-sample drops to 10.0 degC against a 20.0 baseline. Assert that at
capture time rather than letting a silent, dipless fixture make the graph tests
pass for the wrong reason.
"""
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cpva_api as api                                            # noqa: E402

PV = "L3-UTIL-CHL03-006:Temp"
DAY = dt.date(2026, 9, 23)
FROM_H, TO_H = 6, 13
DIPS = ("09:09:51", "11:47:18", "11:54:20")


def main(out_path: Path) -> int:
    tz = api.TZ_PRAGUE
    start = int(dt.datetime(DAY.year, DAY.month, DAY.day, FROM_H,
                            tzinfo=tz).timestamp() * 1e9)
    end = int(dt.datetime(DAY.year, DAY.month, DAY.day, TO_H,
                          tzinfo=tz).timestamp() * 1e9)

    pts = []
    for s in api.cpva_fetch_samples_chunked(PV, start, end, 30.0):
        v = api.cpva_decode_value(s)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        t = s.get("time")
        if not isinstance(t, (int, float)) or not t:
            continue
        pts.append((int(t), float(v)))
    pts.sort()

    lows = [(t, v) for t, v in pts if v <= 15.0]
    seen = {api.ns_to_prague(t).strftime("%H:%M:%S") for t, _ in lows}
    assert len(pts) > 20_000, f"only {len(pts)} readings — window or PV wrong"
    assert all(v == 10.0 for _, v in lows), f"unexpected low values: {set(v for _, v in lows)}"
    for want in DIPS:
        assert want in seen, f"dip at {want} missing — fixture is not worth keeping"
    # The most common reading, not the largest: the chiller also throws two
    # single samples at 29.9, which are the same kind of one-off excursion as
    # the 10.0 dips and must not be mistaken for the resting value.
    baseline = Counter(v for _, v in pts).most_common(1)[0][0]
    assert 19.9 <= baseline <= 20.2, f"baseline is {baseline}, expected ~20"

    out_path.write_text(json.dumps({
        "pv": PV,
        "captured_from": api.ns_to_prague_str(pts[0][0]),
        "captured_to": api.ns_to_prague_str(pts[-1][0]),
        "dips_at": sorted(seen),
        "t_ns": [t for t, _ in pts],
        "values": [v for _, v in pts],
    }), encoding="utf-8")

    print(f"{len(pts)} readings -> {out_path}")
    print(f"  {api.ns_to_prague_str(pts[0][0])} .. {api.ns_to_prague_str(pts[-1][0])}")
    print(f"  dips at {', '.join(sorted(seen))} (all 10.0), baseline {baseline}")
    print(f"  {out_path.stat().st_size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(__file__).with_name("fixture_chiller_dips.json")
    raise SystemExit(main(out))
