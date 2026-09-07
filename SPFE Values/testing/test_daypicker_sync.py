"""Pick days here must be the SAME calendar as everywhere else in the suite.

`Image Tools/daypicker.py` owns how a day is picked for the whole suite. This
folder keeps a verbatim copy of it, because the builder only bundles .py files
that sit in the program's own folder (Dev Tools/b_t.py passes that folder as
PyInstaller's only --paths).

Two copies means they can drift, and a drifted calendar is invisible until an
operator notices that two programs behave differently. So: this test compares
them byte for byte and fails the moment they differ. If it fails, copy the
master over the copy -- do not "fix" one side:

    copy "Image Tools\\daypicker.py" "SPFE Values\\daypicker.py"

No Qt, no network:

    python testing/test_daypicker_sync.py
"""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # SPFE Values/
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
    src = (HERE / "spfe_t.py").read_text(encoding="utf-8", errors="ignore")
    check("spfe_t.py loads it by path, not by a plain import",
          'spec_from_file_location("daypicker"' in src
          and "\nimport daypicker" not in src
          and "\nfrom daypicker import" not in src)

    if FAILURES:
        print("\nFAILED:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
