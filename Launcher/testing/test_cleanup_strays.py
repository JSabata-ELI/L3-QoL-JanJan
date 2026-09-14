"""The broom button tidies up version files that sit in the wrong place.

Run from the Launcher folder:  python testing/test_cleanup_strays.py

Two kinds of stray are found. An exe with a timestamp in the program folder
belongs in archive\\ — that one was already handled. The new one is a file
lying loose in archive\\ itself, next to the vX.Y.Z folders: neither the
version list nor the old cleanup ever looked there, so a stray copy of a build
could sit in the archive forever, invisible and unreachable.

An identical twin of the file already in its version folder is deleted. Any
other stray is moved into its version folder, and if a file of the same name
but a different size is already there, both are kept (" (2)").
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import l  # noqa: E402


def touch(p: Path, body: str = "x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def new_program() -> Path:
    prog = Path(tempfile.mkdtemp()) / "Fake Program"
    prog.mkdir(parents=True)
    return prog


def run_cleanup(prog: Path):
    found = l.find_misplaced_version_files({"Fake Program": {"program_dir": prog}})
    errors = l.apply_misplaced_cleanup(found)
    assert not errors, errors
    return found


# ------------------------------------------------------- an identical twin ----
def test_duplicate_in_archive_root_is_deleted():
    prog = new_program()
    touch(prog / "archive" / "v3.0.8" / "Fake Program v3.0.8.exe", "build 308")
    touch(prog / "archive" / "v3.0.8" / "Fake Program v3.0.8.py", "src 308")
    stray = prog / "archive" / "Fake Program v3.0.8.exe"
    touch(stray, "build 308")

    found = run_cleanup(prog)

    assert found == [(stray, None)], found
    assert not stray.exists(), "the duplicate is still there"
    assert (prog / "archive" / "v3.0.8" / "Fake Program v3.0.8.exe").read_text(
        encoding="utf-8") == "build 308", "the real copy was touched"
    print("  identical twin: deleted, the version folder untouched")


# ------------------------------------------------- a stray with no home yet ----
def test_stray_without_version_folder_is_moved_in():
    prog = new_program()
    touch(prog / "archive" / "v1.0.0" / "Fake Program v1.0.0.exe")
    stray = prog / "archive" / "Fake Program v2.4.1.exe"
    touch(stray, "build 241")

    run_cleanup(prog)

    moved = prog / "archive" / "v2.4.1" / "Fake Program v2.4.1.exe"
    assert not stray.exists()
    assert moved.read_text(encoding="utf-8") == "build 241"
    # and now the launcher can actually see it
    labels = [v["label"] for v in l.scan_archive_versions(prog)]
    assert "v2.4.1" in labels, labels
    print("  homeless stray: moved into its own version folder, now listed")


# ---------------------------------------------- same name, different build ----
def test_different_build_is_kept_beside():
    prog = new_program()
    touch(prog / "archive" / "v3.0.8" / "Fake Program v3.0.8.exe", "build 308")
    stray = prog / "archive" / "Fake Program v3.0.8.exe"
    touch(stray, "a different build of 308")

    run_cleanup(prog)

    assert not stray.exists()
    assert (prog / "archive" / "v3.0.8" / "Fake Program v3.0.8.exe").read_text(
        encoding="utf-8") == "build 308", "the original was overwritten"
    assert (prog / "archive" / "v3.0.8" / "Fake Program v3.0.8 (2).exe").read_text(
        encoding="utf-8") == "a different build of 308", "the other build was lost"
    print("  two different builds: both kept, the original still wins")


# --------------------------------------------------- what must be left alone --
def test_the_rest_of_the_archive_is_left_alone():
    prog = new_program()
    keep = [
        prog / "archive" / "Fake Program v1.2.1__20260511_125840.exe",  # old flat archive
        prog / "archive" / "archive_log.txt",
        prog / "archive" / "notes.md",
        prog / "archive" / "v1.0.0" / "Fake Program v1.0.0.exe",
        prog / "Fake Program v3.0.9.exe",                               # the current build
        prog / "helper.py",
    ]
    for p in keep:
        touch(p)

    found = run_cleanup(prog)

    assert found == [], found
    for p in keep:
        assert p.exists(), f"moved something it should not have: {p.name}"
    print("  everything else: untouched")


# ------------------------------------------- the old case still works too ----
def test_timestamped_exe_in_program_folder_still_moves():
    prog = new_program()
    stray = prog / "Fake Program v1.2.1__20260511_125840.exe"
    touch(stray)

    run_cleanup(prog)

    assert not stray.exists()
    assert (prog / "archive" / stray.name).exists()
    print("  timestamped exe in the program folder: still moved to archive")


if __name__ == "__main__":
    test_duplicate_in_archive_root_is_deleted()
    test_stray_without_version_folder_is_moved_in()
    test_different_build_is_kept_beside()
    test_the_rest_of_the_archive_is_left_alone()
    test_timestamped_exe_in_program_folder_still_moves()
    print("OK")
