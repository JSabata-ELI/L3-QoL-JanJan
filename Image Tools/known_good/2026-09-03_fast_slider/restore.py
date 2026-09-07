"""Compare or restore the known-good Image Slider snapshot of 2026-09-03.

Run with no argument (or --check) to see whether the live files in
Image Tools/ still match this frozen copy. Run with --restore to put this copy
back; the live files are moved aside first, into a timestamped folder under
known_good/_replaced/, so nothing is thrown away.

    python restore.py --check
    python restore.py --restore
"""

import argparse
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent          # known_good/<snapshot>/
LIVE = HERE.parent.parent                       # Image Tools/
BACKUPS = HERE.parent / "_replaced"             # known_good/_replaced/

FILES = ["is_t.py", "cpva_client.py", "img_scale.py", "daypicker.py", "icon.ico"]


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def compare():
    """Return a list of (name, state) with state one of same / differs / missing."""
    rows = []
    for name in FILES:
        live, kept = LIVE / name, HERE / name
        if not live.exists():
            rows.append((name, "missing"))
        elif digest(live) == digest(kept):
            rows.append((name, "same"))
        else:
            rows.append((name, "differs"))
    return rows


def check():
    rows = compare()
    width = max(len(n) for n, _ in rows)
    for name, state in rows:
        mark = {"same": "=", "differs": "!", "missing": "?"}[state]
        print(f"  {mark} {name.ljust(width)}  {state}")
    changed = [n for n, s in rows if s != "same"]
    print()
    if changed:
        print("Live files no longer match the snapshot:", ", ".join(changed))
        print("Run  python restore.py --restore  to put the snapshot back.")
    else:
        print("Live files are identical to the snapshot.")
    return 1 if changed else 0


def restore():
    rows = compare()
    changed = [n for n, s in rows if s != "same"]
    if not changed:
        print("Nothing to do — live files already match the snapshot.")
        return 0

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    keep = BACKUPS / stamp
    keep.mkdir(parents=True, exist_ok=True)

    for name in changed:
        live = LIVE / name
        if live.exists():
            shutil.copy2(live, keep / name)
        shutil.copy2(HERE / name, live)
        print(f"  restored {name}")

    print()
    print(f"Replaced files were saved in: {keep}")
    print("Restart Image Tools for the change to take effect.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="only compare (default)")
    ap.add_argument("--restore", action="store_true", help="put the snapshot back")
    args = ap.parse_args()

    print(f"snapshot: {HERE.name}")
    print(f"live:     {LIVE}")
    print()
    return restore() if args.restore else check()


if __name__ == "__main__":
    sys.exit(main())
