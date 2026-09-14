"""The Spectra tab's calendar must stay the SAME calendar as Image Tools'.

`Image Tools/daypicker.py` owns how a day and a time window are picked for the
whole suite. This folder keeps a verbatim copy of it, because the builder only
bundles .py files that sit in the program's own folder (Dev Tools/b_t.py passes
that folder as PyInstaller's only --paths).

Two copies means they can drift, and a drifted calendar is invisible until an
operator notices that Spectra and the Image Slider behave differently. So: this
test compares them byte for byte and fails the moment they differ. If it fails,
copy the master over the copy — do not "fix" one side:

    copy "Image Tools\\daypicker.py" "CSS Logger\\daypicker.py"

No Qt, no network:

    python testing/test_daypicker_sync.py
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # CSS Logger/
REPO = HERE.parent                                     # the programs root

COPY   = HERE / "daypicker.py"
MASTER = REPO / "Image Tools" / "daypicker.py"

FAILURES: "list[str]" = []


def check(name: str, cond: bool, detail: str = ""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(f"{name} {detail}".strip())


def _first_difference(a: bytes, b: bytes) -> str:
    """Name the first differing line, so the report says WHERE they drifted."""
    la, lb = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(la, lb), start=1):
        if x != y:
            return (f"line {i}\n"
                    f"      copy:   {x[:100]!r}\n"
                    f"      master: {y[:100]!r}")
    if len(la) != len(lb):
        longer = "copy" if len(la) > len(lb) else "master"
        return f"same up to line {min(len(la), len(lb))}, then {longer} has more lines"
    return "no line differs (line endings only)"


def _deployed_layout():
    """Rebuild what the share actually looks like, and load from it.

    A deployed program is an exe with an _internal folder beside it — and that
    _internal is the SHARED runtime library, so daypicker.py is not in it. The
    loose copy the deploy drops next to the exe is. This lays that out in a
    temporary folder, runs the loader's own search over it with the module code
    lifted straight out of sp_t.py, and checks it comes back with the file.
    """
    import ast
    import subprocess
    import tempfile
    import textwrap

    src = (HERE / "sp_t.py").read_text(encoding="utf-8", errors="ignore")
    fn = next((n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.FunctionDef) and n.name == "_import_daypicker"),
              None)
    if fn is None:
        return False, "no _import_daypicker in sp_t.py"
    body = ast.get_source_segment(src, fn)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "_internal").mkdir()          # the shared library — no daypicker
        (root / "daypicker.py").write_bytes(COPY.read_bytes())
        probe = root / "_internal" / "probe.py"
        # What a frozen app sees: sys.executable is the exe in the program
        # folder, sys._MEIPASS and __file__ are both inside _internal.
        probe.write_text(
            "import os, sys\n"
            "sys.frozen = True\n"
            "sys._MEIPASS = os.path.dirname(os.path.abspath(__file__))\n"
            "sys.executable = os.path.join(\n"
            "    os.path.dirname(sys._MEIPASS), 'CSS Logger.exe')\n"
            + textwrap.dedent(body) + "\n"
            "m = _import_daypicker()\n"
            "print(os.path.abspath(m.__file__))\n",
            encoding="utf-8")
        r = subprocess.run([sys.executable, str(probe)],
                           capture_output=True, text=True, cwd=str(root))
    if r.returncode != 0:
        return False, (r.stderr or "").strip().splitlines()[-1:] and \
            (r.stderr or "").strip().splitlines()[-1] or "loader raised"
    return True, r.stdout.strip()


def main() -> int:
    print("test_daypicker_sync")

    check("master exists", MASTER.is_file(), str(MASTER))
    check("copy exists", COPY.is_file(), str(COPY))
    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1

    a, b = COPY.read_bytes(), MASTER.read_bytes()
    same = a == b
    check("the two copies are identical", same,
          "" if same else _first_difference(a, b))
    if not same:
        print(f"      copy   {len(a)} B  sha1 {hashlib.sha1(a).hexdigest()[:12]}")
        print(f"      master {len(b)} B  sha1 {hashlib.sha1(b).hexdigest()[:12]}")

    # The copy is loaded by path, never by `import daypicker`: a plain import
    # would make the builder's module-home check see one module name in two
    # program folders and refuse to build.
    sp_src = (HERE / "sp_t.py").read_text(encoding="utf-8", errors="ignore")
    check("sp_t.py loads it by path, not by a plain import",
          "spec_from_file_location(\"daypicker\"" in sp_src
          and "\nimport daypicker" not in sp_src
          and "\nfrom daypicker import" not in sp_src)

    # A built copy cannot rely on the file being next to sp_t.py. That folder is
    # _internal, and the deploy REPLACES _internal with one shared runtime
    # library (Dev Tools/cm_t.py, "Deploy Libraries") that carries no
    # daypicker.py — which is exactly how the network copy lost it. Two things
    # keep it alive: the module is compiled into the exe, and the loader knows
    # more than one place to look.
    cfg = json.loads((HERE / "build_config.json").read_text(encoding="utf-8"))
    check("build_config.json compiles daypicker into the exe",
          "daypicker" in cfg.get("hidden_imports", []),
          f"hidden_imports = {cfg.get('hidden_imports')}")
    check("the loader falls back to the compiled-in copy",
          'import_module("daypicker")' in sp_src)
    check("the loader looks beside the exe as well",
          "_MEIPASS" in sp_src and "sys.executable" in sp_src)

    # Same fault, same fix, in the other two programs that carry a copy.
    for prog, mod in (("Image Tools", "is_t.py"), ("Image Tools", "if_t.py")):
        src = (REPO / prog / mod).read_text(encoding="utf-8", errors="ignore")
        check(f"{prog}/{mod} searches more than one folder",
              "_MEIPASS" in src and 'import_module("daypicker")' in src)

    check("the loader really finds a copy beside the exe", *_deployed_layout())

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
