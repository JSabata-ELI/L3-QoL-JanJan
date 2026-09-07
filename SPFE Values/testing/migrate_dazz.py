"""
Rename the DAZZ1 columns after the dazzler lost its second number.

WHY
A dazzler has one percentage, not two. Until 2026-09-03 spfe_fields.json gave
DAZZ1 two PVs (Power_RB and a guessed Amplitude_RB "intensity"), and a row with
two numbers is stored under two column names -- dazz1_1 and dazz1_2. With one PV
the column is simply dazz1, so every value already recorded would be orphaned:
the log would still hold it, the table would no longer look for it.

This script renames dazz1_1 to dazz1 and drops dazz1_2, in

  * spfe_log.csv          the measured values
  * spfe_references.json  the range somebody set for the number

both in the shared folder and in the local %APPDATA%\\SPFE_Values copy. It is
meant to be run once, and it is safe to run again: a file with no dazz1_1 in it
is left alone.

  python testing/migrate_dazz.py --diff     what would change
  python testing/migrate_dazz.py --write    change it

Every file is copied beside itself as *.before-dazz-<stamp> before it is
rewritten.
"""
import csv
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import spfe_store as store                                    # noqa: E402

OLD_KEEP = "dazz1_1"        # the Power number: becomes dazz1
OLD_DROP = "dazz1_2"        # the "intensity" that never existed
NEW = "dazz1"


def _stamp() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}"


def _backup(path: Path) -> None:
    dest = path.with_name(f"{path.name}.before-dazz-{_stamp()}")
    shutil.copy2(path, dest)
    print(f"    backed up to {dest.name}")


def migrate_csv(path: Path, write: bool) -> bool:
    """Returns True when the file needed changing."""
    if not path.exists():
        print(f"  {path}: not there")
        return False
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or [])
        rows = list(reader)
    if OLD_KEEP not in header and OLD_DROP not in header:
        print(f"  {path.name}: already migrated ({len(rows)} rows)")
        return False

    filled = sum(1 for r in rows if (r.get(OLD_KEEP) or "").strip())
    dropped = sum(1 for r in rows if (r.get(OLD_DROP) or "").strip())
    print(f"  {path.name}: {len(rows)} rows, {filled} DAZZ1 values kept, "
          f"{dropped} intensity values dropped")

    new_header = []
    for name in header:
        if name == OLD_DROP:
            continue
        new_header.append(NEW if name == OLD_KEEP else name)
    # A file that already has a dazz1 column as well as dazz1_1 would end up
    # with the name twice; that cannot happen from this program, but a
    # duplicate header silently loses data, so it is refused rather than fixed.
    if len(set(new_header)) != len(new_header):
        print(f"  REFUSING {path.name}: it already has a {NEW} column.")
        raise SystemExit(1)

    if not write:
        return True

    _backup(path)
    tmp = path.with_name(path.name + ".migrating")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=new_header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {k: v for k, v in row.items() if k not in (OLD_KEEP, OLD_DROP)}
            out[NEW] = row.get(OLD_KEEP, "")
            writer.writerow(out)
    tmp.replace(path)
    print(f"    rewrote {path.name}")
    return True


def migrate_refs(path: Path, write: bool) -> bool:
    if not path.exists():
        print(f"  {path}: not there")
        return False
    entries = store.read_json_map(path, "limits")
    if OLD_KEEP not in entries and OLD_DROP not in entries:
        print(f"  {path.name}: already migrated ({len(entries)} ranges)")
        return False
    print(f"  {path.name}: {OLD_KEEP} -> {NEW}"
          + (f", {OLD_DROP} dropped" if OLD_DROP in entries else ""))
    if not write:
        return True

    _backup(path)
    out = {k: v for k, v in entries.items() if k not in (OLD_KEEP, OLD_DROP)}
    if OLD_KEEP in entries:
        out[NEW] = entries[OLD_KEEP]
    store.write_json_map(path, "limits", out)
    print(f"    rewrote {path.name}")
    return True


def folders() -> list[Path]:
    out = [store.local_dir()]
    cfg = store.load_config()
    root, how = store.resolve_share(cfg)
    if root:
        folder = store.share_dir(root, cfg)
        print(f"share: {folder}  ({how})")
        out.append(folder)
    else:
        print("share: neither name answered - only the local copy is migrated.")
    return out


def main() -> int:
    write = "--write" in sys.argv
    if not write and "--diff" not in sys.argv:
        print(__doc__)
        return 0

    changed = False
    for folder in folders():
        print(f"\n{folder}")
        changed |= migrate_csv(folder / store.CSV_NAME, write)
        changed |= migrate_refs(folder / store.REFERENCES_NAME, write)

    if not changed:
        print("\nNothing to do.")
    elif not write:
        print("\nDry run. Add --write to change the files.")
    else:
        print("\nDone. Now run testing/rebuild_workbook.py --diff")
    return 0


if __name__ == "__main__":
    sys.exit(main())
