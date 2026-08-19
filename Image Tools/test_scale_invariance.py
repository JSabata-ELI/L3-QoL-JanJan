"""Check the archive's intensity scale on real frames — run this from the lab.

The absolute-scale rendering rests on two claims about how the archiver stores a frame:

    stored_16bit = raw_counts * 65535 / (2**bits - 1)     with bits fixed WITHIN a frame,
    bits = ceil(log2(MaxValue + 1))                       the bracket of that frame's peak

Three things are asserted per frame:
  * `stored_max / MaxValue` lands within 0.5 % of a tabulated 65535/(2**bits-1) factor,
    and dividing the stored maximum back by it returns the `MaxValue` integer;
  * that same bracket is what `img_scale.bits_from_max_value` predicts from `MaxValue`
    ALONE — the preview layer has no full-resolution maximum and must rely on the rule,
    so a mismatch means preview and refined render could pick different ranges;
  * unsaturated frames exist at all (stored_max < 65535) — a per-frame stretch onto the
    full range would pin every single frame's maximum to 65535.

And one thing is REPORTED per camera: how many frames sit in a different bracket from
the camera's largest. Those are the frames whose brightness would double or halve on
screen without the reference-range correction (C03-081-PCW3NF, whose peak sits on
1023/1024, is the camera that made this visible). A camera drifting onto a power of two
shows up here before anyone notices coloured flicker.

Not shipped: the builder keeps `test_*` out of the bundle.

Usage — one camera-hour, or a whole day:

    python test_scale_invariance.py "\\\\users-L3.tier0.lcs.local\\cpva-image-2026\\2026\\08\\14\\05"
    python test_scale_invariance.py "\\\\users-L3.tier0.lcs.local\\cpva-image-2026\\2026\\08\\14" --limit 400

Exit code is 1 when a frame violates the rule, so it can gate a build.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import img_scale  # noqa: E402  (path set above so this runs from anywhere)


_IMG_SUFFIXES = {".png", ".tif", ".tiff"}


def frames(roots, limit):
    seen = 0
    for r in roots:
        p = Path(r)
        paths = sorted(p.rglob("*.png")) if p.is_dir() else [p]
        for f in paths:
            # The share carries housekeeping entries (.isi_s3_dir); reporting those as
            # scale violations would gate a build on a file that is not a frame.
            if f.suffix.lower() not in _IMG_SUFFIXES:
                continue
            yield f
            seen += 1
            if limit and seen >= limit:
                return


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="archive folder(s) or file(s)")
    ap.add_argument("--limit", type=int, default=0, help="stop after N frames")
    ap.add_argument("-v", "--verbose", action="store_true", help="one line per frame")
    args = ap.parse_args(argv)

    n = matched = unsaturated = 0
    no_meta = []
    bad = []
    depths: dict[int, int] = {}
    cameras: dict[str, set] = {}

    for f in frames(args.paths, args.limit):
        try:
            with Image.open(str(f)) as im:
                info = dict(im.info)
                if im.mode not in ("I", "I;16"):
                    continue
                arr = np.asarray(im, dtype=np.float64)
        except Exception as e:
            bad.append((f, f"open failed: {type(e).__name__}: {e}"))
            continue
        n += 1
        meta = img_scale.meta_from_info(info, arr)
        if meta.max_value is None:
            no_meta.append(f)
            continue
        if meta.factor is None:
            ratio = meta.stored_max / meta.max_value
            bad.append((f, f"ratio {ratio:.4f} matches no known factor "
                           f"(MaxValue={meta.max_value:.0f}, "
                           f"stored_max={meta.stored_max:.0f})"))
            continue
        matched += 1
        depths[meta.bit_depth] = depths.get(meta.bit_depth, 0) + 1
        cam = img_scale.camera_from_path(f) or f.parent.name
        cameras.setdefault(cam, []).append(meta.bit_depth)
        if meta.stored_max < img_scale.FULL_SCALE_16:
            unsaturated += 1
        recovered = meta.stored_max / meta.factor
        if abs(recovered - meta.max_value) > 1.0:
            bad.append((f, f"recovered peak {recovered:.2f} != MaxValue "
                           f"{meta.max_value:.0f}"))
        # The metadata-only rule the preview layer depends on.
        predicted = img_scale.bits_from_max_value(meta.max_value)
        if predicted != meta.bit_depth:
            bad.append((f, f"MaxValue {meta.max_value:.0f} predicts {predicted}-bit "
                           f"but the pixels say {meta.bit_depth}-bit"))
        if args.verbose:
            print(f"{f.name:60s} {meta.scale_note()}")

    print(f"16-bit frames scanned   : {n}")
    print(f"factor matched          : {matched}")
    print(f"unsaturated (max<65535) : {unsaturated}"
          f"{'' if unsaturated else '   <-- suspicious: check for per-frame stretching'}")
    print(f"bit depths seen         : "
          f"{', '.join(f'{b}-bit x{c}' for b, c in sorted(depths.items())) or '-'}")
    straddling = 0
    for cam, ds in sorted(cameras.items()):
        if len(set(ds)) <= 1:
            continue
        straddling += 1
        ref = max(ds)
        off = sum(1 for d in ds if d != ref)
        print(f"  {cam}: straddles a bracket — {off} of {len(ds)} frames are not "
              f"{ref}-bit ({', '.join(f'{d}-bit x{ds.count(d)}' for d in sorted(set(ds)))}). "
              f"Those frames render at {2 ** (ref - min(ds))}x the brightness of the rest "
              f"unless the camera's reference range is applied.")
    print(f"cameras straddling      : {straddling}")
    print(f"no MaxValue tEXt        : {len(no_meta)}")
    for p in no_meta[:5]:
        print(f"    {p}")
    print(f"violations              : {len(bad)}")
    for p, why in bad[:20]:
        print(f"    {p}: {why}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
