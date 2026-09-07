"""List the version resource of every deployed exe -- this is the text the
taskbar button is named after when no shortcut names the app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from audit_exe_identity import SHARE, SKIP_DIRS, version_strings  # noqa: E402

for d in sorted(SHARE.iterdir()):
    if not d.is_dir() or d.name in SKIP_DIRS:
        continue
    for exe in sorted(d.glob("*.exe")):
        vi = version_strings(exe)
        desc = vi.get("FileDescription") if vi else None
        print(f"{exe.name:<40} FileDescription={desc!r} "
              f"ProductName={vi.get('ProductName')!r} "
              f"FileVersion={vi.get('FileVersion')!r}" if vi else
              f"{exe.name:<40} NO VERSION RESOURCE")
