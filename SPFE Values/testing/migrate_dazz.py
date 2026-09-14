"""
Move the recorded dazzler percentage to the row it really belongs to.

WHY
There is one dazzler in the archiver, L3-SPFE-AOD03-002, and it is DAZZ2 --
confirmed 2026-09-07. Until then spfe_fields.json read its Power_RB into the
DAZZ1 row, so every value recorded so far sits in the wrong line of the table.

The channel also archives the percentage as a fraction -- 0.33 for 33 % -- and
the field file now scales it by 100 on the way in, so the days already recorded
have to be scaled as well.

This script moves the values from dazz1 to dazz2, scales them to percent and
leaves dazz1 empty, in

  * spfe_log.csv          the measured values
  * spfe_references.json  the range somebody set for the number

both in the shared folder and in the local %APPDATA%\\SPFE_Values copy.

It also still performs the older, one-off rename this file was written for:
dazz1_1 -> dazz1 and dazz1_2 dropped, from the days DAZZ1 had two PVs. Both
steps are safe to run again -- a file that has nothing to move is left alone.

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

OLD_KEEP = "dazz1_1"        # the Power number of the two-PV days: becomes dazz1
OLD_DROP = "dazz1_2"        # the "intensity" that never existed
SRC = "dazz1"               # where the percentage used to be recorded
DST = "dazz2"               # the dazzler the archiver actually knows

# The channel archives the percentage as a fraction (0.33 = 33 %). The field
# file now scales it by 100 on the way in, so the days already recorded have to
# be scaled too or the row would mix 0.33 with 33. A value above this ceiling is
# already a percentage and is left alone, which is what makes the step safe to
# run twice -- the dazzler runs at tens of percent, never below 1.5 %.
SCALE = 100.0
FRACTION_CEILING = 1.5


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

    # Step 1: the old two-PV rename, for files that never had it.
    renamed = OLD_KEEP in header or OLD_DROP in header
    if renamed:
        kept = sum(1 for r in rows if (r.get(OLD_KEEP) or "").strip())
        dropped = sum(1 for r in rows if (r.get(OLD_DROP) or "").strip())
        print(f"  {path.name}: {kept} values moved out of {OLD_KEEP}, "
              f"{dropped} intensity values dropped")
        header = [n for n in header if n != OLD_DROP]
        header = [SRC if n == OLD_KEEP else n for n in header]
        if len(set(header)) != len(header):
            print(f"  REFUSING {path.name}: it already has a {SRC} column.")
            raise SystemExit(1)
        for r in rows:
            r[SRC] = r.get(OLD_KEEP, "")
            r.pop(OLD_KEEP, None)
            r.pop(OLD_DROP, None)

    # Step 2: the percentage belongs to DAZZ2.
    movable = [r for r in rows if (r.get(SRC) or "").strip()]
    blocked = [r for r in movable if (r.get(DST) or "").strip()]
    if blocked:
        print(f"  REFUSING {path.name}: {len(blocked)} rows already have a "
              f"{DST} value; moving would overwrite it.")
        raise SystemExit(1)
    moved = len(movable)
    if moved:
        print(f"  {path.name}: {len(rows)} rows, {moved} values moved "
              f"{SRC} -> {DST}")
        if DST not in header:
            header.insert(header.index(SRC) + 1, DST)
        for r in movable:
            r[DST] = r[SRC]
            r[SRC] = ""

    # Step 3: the fraction becomes a percentage.
    scaled = 0
    for r in rows:
        raw = (r.get(DST) or "").strip()
        if not raw:
            continue
        try:
            v = float(raw)
        except ValueError:
            continue
        if abs(v) <= FRACTION_CEILING:
            r[DST] = f"{v * SCALE:g}"
            scaled += 1
    if scaled:
        print(f"  {path.name}: {scaled} {DST} values scaled x{SCALE:g} "
              f"(fraction -> percent)")

    if not (renamed or moved or scaled):
        print(f"  {path.name}: already migrated ({len(rows)} rows)")
        return False

    if not write:
        return True

    _backup(path)
    tmp = path.with_name(path.name + ".migrating")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in header})
    tmp.replace(path)
    print(f"    rewrote {path.name}")
    return True


def migrate_refs(path: Path, write: bool) -> bool:
    if not path.exists():
        print(f"  {path}: not there")
        return False
    entries = store.read_json_map(path, "limits")
    out = dict(entries)
    changed = False

    if OLD_KEEP in out or OLD_DROP in out:
        print(f"  {path.name}: {OLD_KEEP} -> {SRC}"
              + (f", {OLD_DROP} dropped" if OLD_DROP in out else ""))
        keep = out.pop(OLD_KEEP, None)
        out.pop(OLD_DROP, None)
        if keep is not None:
            out[SRC] = keep
        changed = True

    if SRC in out:
        if DST in out:
            print(f"  REFUSING {path.name}: {DST} already has a range.")
            raise SystemExit(1)
        print(f"  {path.name}: {SRC} -> {DST}")
        out[DST] = out.pop(SRC)
        changed = True

    if not changed:
        print(f"  {path.name}: already migrated ({len(entries)} ranges)")
        return False
    if not write:
        return True

    _backup(path)
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
