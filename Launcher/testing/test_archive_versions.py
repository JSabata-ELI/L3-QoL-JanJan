"""What the version menu offers for each shape of archive folder.

Run from the Launcher folder:  python testing/test_archive_versions.py

A version folder that kept only its source files used to be left out of the
menu entirely, because the scan demanded an .exe. Those are exactly the ones
that can still be started truthfully, so they have to be listed.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import l  # noqa: E402


def touch(p: Path, body: str = "x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def build_archive() -> Path:
    prog = Path(tempfile.mkdtemp()) / "Fake Program"
    arch = prog / "archive"

    # exe plus its sources — the normal deploy
    touch(arch / "v2.5.4" / "Fake Program v2.5.4.exe")
    touch(arch / "v2.5.4" / "Fake Program v2.5.4.py")
    touch(arch / "v2.5.4" / "helper.py")

    # sources only — used to be invisible in the menu
    touch(arch / "v2.5.3" / "Fake Program v2.5.3.py")
    touch(arch / "v2.5.3" / "helper.py")

    # exe only
    touch(arch / "v2.4.0" / "Fake Program v2.4.0.exe")

    # a snapshot that brought its own _internal — starts as it lies
    touch(arch / "v2.3.0" / "Fake Program v2.3.0.exe")
    touch(arch / "v2.3.0" / "_internal" / "base_library.zip")

    # a " (2)" duplicate must never win over the plain name
    touch(arch / "v2.2.0" / "Fake Program v2.2.0 (2).py")
    touch(arch / "v2.2.0" / "Fake Program v2.2.0.py")

    # helper files stranded by an old deploy — not a version
    touch(arch / "unknown" / "Fake Program v1.0.0.py")
    touch(arch / "unknown" / "archive_log.txt")

    return prog


def main():
    prog = build_archive()
    out = l.scan_archive_versions(prog)

    for e in out:
        exe = e["exe_path"].name if e["exe_path"] else "-"
        py = e["py_path"].name if e["py_path"] else "-"
        print(f"  {e['label']:<10} exe={exe:<28} py={py:<30} "
              f"has _internal={e['has_internal']}")

    labels = [e["label"] for e in out]
    assert labels == ["v2.5.4", "v2.5.3", "v2.4.0", "v2.3.0", "v2.2.0"], labels

    by_label = {e["label"]: e for e in out}

    # sources-only folder is listed, and points at its main script
    assert by_label["v2.5.3"]["exe_path"] is None
    assert by_label["v2.5.3"]["py_path"].name == "Fake Program v2.5.3.py"

    # exe-only folder (the legacy shape) is listed and runnable on its own
    assert by_label["v2.4.0"]["py_path"] is None

    assert by_label["v2.3.0"]["has_internal"] is True
    assert by_label["v2.5.4"]["has_internal"] is False

    # the canonical name, not the " (2)" copy
    assert by_label["v2.2.0"]["py_path"].name == "Fake Program v2.2.0.py"

    print("OK")


if __name__ == "__main__":
    main()
