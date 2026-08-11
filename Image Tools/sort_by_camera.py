"""Sort camera snapshots from per-shot folders into one folder per camera.

Input layout (what the cameras/Screenshots tool produces):

    <source>/
        23072026_164922/
            C03-040-PFM13NF-_-IMG_2026-07-23_16-49-22-047.png
            C03-041-PASF1NF-_-IMG_2026-07-23_16-49-22-204.png
            ...
        24072026_165357/
            ...

Output layout:

    <source>/final/
        PFM13NF/
            C03-040-PFM13NF-_-IMG_2026-07-23_16-49-22-047.png
            C03-040-PFM13NF-_-IMG_2026-07-24_16-53-57-697.png
            ...
        PASF1NF/
            ...

File names keep the acquisition timestamp, so alphabetical order inside each
camera folder is chronological order.

Usage:
    python sort_by_camera.py "C:\\path\\to\\saturday conspiracy"
    python sort_by_camera.py <source> --out <dir> --move --dry-run
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

# 'C03-040-PFM13NF-_-IMG_2026-08-05_17-07-59-690_g.png' -> 'PFM13NF'
# also matches the older 'C03-032-PAP1DF-IMG_...' spelling.
_CAM_RE = re.compile(r"^(?P<prefix>.+?)[-_]-?IMG[_-]", re.IGNORECASE)


def camera_of(name: str) -> str | None:
    """Camera short name from a snapshot file name, or None if unrecognised."""
    m = _CAM_RE.match(name)
    if not m:
        return None
    prefix = m.group("prefix").rstrip("-_")
    # 'C03-040-PFM13NF' -> 'PFM13NF'; a bare 'PFM13NF' stays as it is.
    return prefix.split("-")[-1] or None


def sort_folder(source: Path, out: Path, move: bool = False,
                dry_run: bool = False) -> tuple[int, int]:
    """Copy (or move) every image under `source` into out/<camera>/.

    Returns (handled, skipped).
    """
    handled = skipped = 0
    unknown: set[str] = set()

    for path in sorted(source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            continue
        if out in path.parents:  # never re-process our own output
            continue

        cam = camera_of(path.name)
        if cam is None:
            unknown.add(path.name)
            skipped += 1
            continue

        target_dir = out / cam
        target = target_dir / path.name
        if target.exists():
            skipped += 1
            continue

        print(f"{cam:<12} {path.name}")
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            if move:
                shutil.move(str(path), str(target))
            else:
                shutil.copy2(path, target)
        handled += 1

    for name in sorted(unknown):
        print(f"  ! unrecognised name, skipped: {name}", file=sys.stderr)
    return handled, skipped


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source", type=Path, help="folder holding the dated subfolders")
    p.add_argument("--out", type=Path, default=None,
                   help="output folder (default: <source>/final)")
    p.add_argument("--move", action="store_true",
                   help="move files instead of copying them")
    p.add_argument("--dry-run", action="store_true",
                   help="only list what would happen")
    a = p.parse_args(argv)

    source: Path = a.source.expanduser().resolve()
    if not source.is_dir():
        p.error(f"not a folder: {source}")
    out: Path = (a.out.expanduser().resolve() if a.out else source / "final")

    handled, skipped = sort_folder(source, out, move=a.move, dry_run=a.dry_run)
    verb = "would sort" if a.dry_run else ("moved" if a.move else "copied")
    print(f"\n{verb} {handled} file(s) into {out}" + (f", skipped {skipped}" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
