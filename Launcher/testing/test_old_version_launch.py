"""Starting an older version really starts THAT version's files.

Run from the Launcher folder:  python testing/test_old_version_launch.py

The archived .exe is the whole old program — each build carries its own code,
and the _internal folder beside it is only the shared Python runtime, which any
version can use. So the exe is what gets started; it just has to sit next to an
_internal, which is why an archived version is put into the program folder for
the run.

What used to go wrong: the swap moved every .exe of the program folder out of
the way first — including the very exe that was about to be started, whenever
the chosen version was one of those sitting in the program folder. And only the
main .py was copied in, so the folder ended up with an old exe and no helper
sources at all.
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import l  # noqa: E402

shown: list[tuple] = []
started: list[list[str]] = []


class _MB:
    """Dialogs are recorded, not opened."""
    @staticmethod
    def showerror(*a, **kw):
        shown.append(a)

    @staticmethod
    def showinfo(*a, **kw):
        shown.append(a)


class _FakeApp:
    """Just enough of the launcher window for _launch_exe."""
    status = type("S", (), {"configure": lambda self, *a, **k: None})()

    def after(self, delay, fn, *args):
        if getattr(fn, "__name__", "") in ("showerror", "showinfo"):
            shown.append(args)
            return
        try:
            fn(*args)
        except Exception:
            pass


def touch(p: Path, body: str = "x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def join_workers():
    for t in threading.enumerate():
        if t is not threading.current_thread():
            t.join(timeout=20)


def launch(prog: Path, entry: dict):
    l.Launcher._launch_exe(_FakeApp(), entry.get("exe_path"), prog,
                           entry.get("py_path"), entry.get("has_internal", False))
    join_workers()


# ------------------------------------------------- a version already in place --
def test_exe_in_program_folder_is_not_moved():
    """The published folder holds several exes; picking one must just run it.

    This is the case that used to fail outright: the exe was moved into the
    temporary folder and then copied from the path it had just left.
    """
    shown.clear()
    started.clear()
    prog = Path(tempfile.mkdtemp()) / "Fake Program"
    touch(prog / "Fake Program v3.0.9.exe")
    touch(prog / "Fake Program v3.0.8.exe")
    touch(prog / "Fake Program v3.0.5.exe")
    touch(prog / "Fake Program v3.0.9.py")
    touch(prog / "_internal" / "base_library.zip")

    versions = l._build_version_list(
        [p for p in prog.glob("*.exe")], prog)
    old = [v for v in versions if v["label"] == "v3.0.5"][0]
    launch(prog, old)

    assert not shown, shown
    assert started and Path(started[0][0]).name == "Fake Program v3.0.5.exe", started
    # nothing was moved: all three exes are still there, none staged or lost
    assert sorted(p.name for p in prog.glob("*.exe")) == [
        "Fake Program v3.0.5.exe",
        "Fake Program v3.0.8.exe",
        "Fake Program v3.0.9.exe",
    ]
    assert not (prog / "archive" / "_temp_latest").exists()
    print("  in-folder version: started in place, nothing moved")


# --------------------------------------------------- a version from the archive --
def test_archived_exe_is_swapped_in_with_its_sources():
    """The archived exe runs from the program folder, and its .py ride along."""
    shown.clear()
    started.clear()
    prog = Path(tempfile.mkdtemp()) / "Fake Program"
    touch(prog / "Fake Program v3.0.9.exe", "new exe")
    touch(prog / "Fake Program v3.0.9.py", "new main")
    touch(prog / "helper.py", "new helper")
    touch(prog / "_internal" / "base_library.zip")

    snap = prog / "archive" / "v3.0.4"
    touch(snap / "Fake Program v3.0.4.exe", "old exe")
    touch(snap / "Fake Program v3.0.4.py", "old main")
    touch(snap / "helper.py", "old helper")

    seen: dict = {}

    def _popen(cmd, **kw):
        started.append([str(c) for c in cmd])
        # what the program folder looks like at the moment of the start
        seen["files"] = {p.name: p.read_text(encoding="utf-8", errors="replace")
                         for p in Path(kw["cwd"]).glob("*.*") if p.is_file()}
        return type("P", (), {"returncode": 0, "wait": lambda self: 0})()

    real = l.subprocess.Popen
    l.subprocess.Popen = _popen
    try:
        launch(prog, l.scan_archive_versions(prog)[0])
    finally:
        l.subprocess.Popen = real

    assert not shown, shown
    files = seen["files"]
    print("  during the run:", sorted(files))

    # the old exe was started, from the program folder (next to _internal)
    assert Path(started[0][0]) == prog / "Fake Program v3.0.4.exe", started
    assert files["Fake Program v3.0.4.exe"] == "old exe"
    # its whole source snapshot came with it, helpers included
    assert files["Fake Program v3.0.4.py"] == "old main"
    assert files["helper.py"] == "old helper"
    # and the newest files were out of the way, not sitting beside the old exe
    assert "Fake Program v3.0.9.exe" not in files
    assert "Fake Program v3.0.9.py" not in files

    # afterwards the newest version is back, exactly as it was
    assert (prog / "Fake Program v3.0.9.exe").read_text(encoding="utf-8") == "new exe"
    assert (prog / "Fake Program v3.0.9.py").read_text(encoding="utf-8") == "new main"
    assert (prog / "helper.py").read_text(encoding="utf-8") == "new helper"
    assert not (prog / "Fake Program v3.0.4.exe").exists(), "staged exe left behind"
    assert not (prog / "archive" / "_temp_latest").exists(), "temp folder left behind"
    print("  archived version: swapped in with its sources, everything restored")


# ------------------------------------------------------------ sources only ----
def test_sources_only_version_runs_its_own_code():
    """A version folder with no exe runs its .py — and the OLD ones."""
    shown.clear()
    tmp = Path(tempfile.mkdtemp())
    prog = tmp / "Fake Program"
    marker = tmp / "marker.txt"

    touch(prog / "Fake Program v2.0.0.exe")
    touch(prog / "_internal" / "base_library.zip")
    touch(prog / "Fake Program v2.0.0.py", "raise SystemExit('new code ran')\n")
    touch(prog / "helper.py", "VERSION = 'new'\n")
    touch(prog / "icon.ico")          # an asset only the program folder has

    snap = prog / "archive" / "v1.0.0"
    touch(snap / "Fake Program v1.0.0.py",
          "import helper\n"
          "from pathlib import Path\n"
          f"Path(r'{marker}').write_text(\n"
          "    'ran=' + helper.VERSION + ' icon=' + str(Path('icon.ico').exists()),\n"
          "    encoding='utf-8')\n")
    touch(snap / "helper.py", "VERSION = 'old'\n")

    launch(prog, l.scan_archive_versions(prog)[0])
    for _ in range(200):
        if marker.exists():
            break
        time.sleep(0.05)

    assert not shown, shown
    assert marker.exists(), "the archived sources never ran"
    got = marker.read_text(encoding="utf-8")
    print("  sources only:", got)
    assert "ran=old" in got, "the CURRENT sources ran instead of the archived ones"
    assert "icon=True" in got, "the program folder's files were not reachable"

    assert (prog / "helper.py").read_text(encoding="utf-8").strip() == "VERSION = 'new'", \
        "current helper not restored"
    assert (prog / "Fake Program v2.0.0.exe").exists(), "current exe not restored"
    assert not (prog / "archive" / "_temp_latest").exists(), "temp folder left behind"


# ----------------------------------------------------------- one-file helper --
def test_one_file_program_runs_in_place():
    """No _internal anywhere: the archived exe needs nothing beside it."""
    shown.clear()
    started.clear()
    prog = Path(tempfile.mkdtemp()) / "Fake Helper"
    touch(prog / "Fake Helper v2.0.0.exe")
    touch(prog / "archive" / "v1.0.0" / "Fake Helper v1.0.0.exe")

    launch(prog, l.scan_archive_versions(prog)[0])

    assert not shown, shown
    assert Path(started[0][0]) == prog / "archive" / "v1.0.0" / "Fake Helper v1.0.0.exe", started
    assert not (prog / "archive" / "_temp_latest").exists()
    print("  one-file program: started where it lies")


if __name__ == "__main__":
    l.messagebox = _MB

    # The in-place route starts the exe through ShellExecuteEx. Record the path
    # and report success, so nothing is really started.
    def _fake_shell_exec(p):
        started.append([str(p)])
        return True

    l._launch_no_zone_check = _fake_shell_exec
    test_exe_in_program_folder_is_not_moved()
    test_one_file_program_runs_in_place()
    test_archived_exe_is_swapped_in_with_its_sources()
    test_sources_only_version_runs_its_own_code()
    print("OK")
