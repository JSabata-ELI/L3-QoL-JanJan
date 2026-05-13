"""
sort_archive.py - reorganize an archive/ folder from flat structure to version subfolders.

Usage:
    python sort_archive.py <archive_dir> [--apply]

Without --apply: dry run (just prints what would happen).
With --apply: actually moves files and deletes older builds.

Logic:
- Files with version+timestamp: "Image Tools v1.0.7__20260409_135029.exe"
  -> extract version, timestamp
- Raw py files without version: "if_t__20260407_081206.py"
  -> match timestamp to a versioned file with same timestamp -> get version
- Per version: keep only the LATEST timestamp build, delete older ones
- Result: archive/v1.0.7/  with .exe + .py files for that build

Files with no matching version and no timestamp (e.g. "Image Tools v1.2.1.exe")
are left in place (they are current-version stragglers, handled separately).
"""

import re
import sys
from pathlib import Path
from collections import defaultdict

VERSIONED_RE = re.compile(
    r"^.+\s+(v\d+\.\d+\.\d+)__(\d{8}_\d{6})\.",
    re.IGNORECASE,
)
TIMESTAMPED_ONLY_RE = re.compile(
    r"^.+__(\d{8}_\d{6})\.",
)


def parse_versioned(name: str):
    """Return (version_str, timestamp_str) or None."""
    m = VERSIONED_RE.match(name)
    if m:
        return m.group(1).lower(), m.group(2)
    return None


def parse_timestamp_only(name: str):
    """Return timestamp_str or None for raw files like 'if_t__20260407_081206.py'."""
    m = TIMESTAMPED_ONLY_RE.match(name)
    if m:
        return m.group(1)
    return None


def sort_archive(archive_dir: Path, apply: bool):
    if not archive_dir.exists():
        print("ERROR: %s does not exist" % archive_dir)
        return

    files = [f for f in archive_dir.iterdir() if f.is_file()]

    # Step 1: collect all versioned files grouped by version
    version_builds: dict = defaultdict(lambda: defaultdict(list))
    ts_to_version: dict = {}
    unmatched = []

    for f in files:
        parsed = parse_versioned(f.name)
        if parsed:
            ver, ts = parsed
            version_builds[ver][ts].append(f)
            ts_to_version[ts] = ver
        else:
            unmatched.append(f)

    # Step 2: match raw timestamped files to a version via timestamp
    truly_unmatched = []
    for f in unmatched:
        ts = parse_timestamp_only(f.name)
        if ts and ts in ts_to_version:
            ver = ts_to_version[ts]
            version_builds[ver][ts].append(f)
        else:
            truly_unmatched.append(f)

    # Step 3: for each version keep only the LATEST timestamp build
    actions_move = []   # (src, dst)
    actions_delete = []

    for ver, ts_dict in sorted(version_builds.items()):
        sorted_ts = sorted(ts_dict.keys(), reverse=True)
        latest_ts = sorted_ts[0]
        older_ts = sorted_ts[1:]

        target_dir = archive_dir / ver

        for f in ts_dict[latest_ts]:
            dst = target_dir / f.name
            actions_move.append((f, dst))

        for ts in older_ts:
            for f in ts_dict[ts]:
                actions_delete.append(f)

    # Print summary
    print("\n" + "=" * 60)
    print("Archive: %s" % archive_dir)
    print("=" * 60)
    print("\nVersions found: %s" % sorted(version_builds.keys()))
    print("\n--- MOVE (%d files) ---" % len(actions_move))
    for src, dst in sorted(actions_move, key=lambda x: str(x[1])):
        print("  %s" % src.name)
        print("    -> %s/%s" % (dst.parent.name, dst.name))

    print("\n--- DELETE older builds (%d files) ---" % len(actions_delete))
    for f in sorted(actions_delete, key=lambda x: x.name):
        print("  %s" % f.name)

    print("\n--- LEAVE in place (no version / no matching timestamp) ---")
    for f in sorted(truly_unmatched, key=lambda x: x.name):
        print("  %s" % f.name)

    if not apply:
        print("\n[DRY RUN] - run with --apply to actually move/delete files")
        return

    print("\n[APPLYING changes...]")

    for f in actions_delete:
        print("  DELETE %s" % f.name)
        f.unlink()

    for src, dst in actions_move:
        dst.parent.mkdir(exist_ok=True)
        print("  MOVE %s -> %s/" % (src.name, dst.parent.name))
        src.rename(dst)

    print("\nDone.")


if __name__ == "__main__":
    args = sys.argv[1:]
    apply = "--apply" in args
    paths = [a for a in args if not a.startswith("--")]

    if not paths:
        target = Path(r"Z:\Software\Image Tools\archive")
    else:
        target = Path(paths[0])

    sort_archive(target, apply=apply)
