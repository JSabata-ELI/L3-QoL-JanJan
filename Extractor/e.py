# exctractor.py
# Najde jeden .zip soubor v SOFTWARE_ROOT a rozbalí ho do každé podsložky.
# Použití: spusť skript, potvrď a čekej.

import zipfile
import shutil
from pathlib import Path
from datetime import datetime

SOFTWARE_ROOT = Path(r"Z:\Software")

# Složky do kterých se rozbalí _internal
TARGET_DIRS = [
    "Calibrations",
    "Copy manager",
    "Counter of shots",
    "Image Finder",
    "Image Slider",
    "Image Tools",
    "Launcher",
    "Screenshots",
    "Time converter",
]


def find_zip(root: Path) -> Path | None:
    zips = [f for f in root.iterdir() if f.is_file() and f.suffix.lower() == ".zip"]
    if not zips:
        return None
    if len(zips) > 1:
        print(f"WARNING: Found {len(zips)} zip files, using: {zips[0].name}")
        for z in zips:
            print(f"  - {z.name}")
    return zips[0]


def extract_to(zip_path: Path, dst_dir: Path):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Archivuj starou _internal pokud existuje
    old_internal = dst_dir / "_internal"
    if old_internal.exists():
        try:
            shutil.rmtree(old_internal)
            print(f"  Removed old _internal")
        except Exception as e:
            print(f"  ERROR: Could not remove old _internal: {e}")
            return False

    # Rozbal zip
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.infolist()
            total = len(members)
            # Zjisti prefix který je potřeba oříznout (např. "_internal_builder/_internal/")
            prefix = ""
            for m in members:
                if m.filename.endswith("/"):
                    continue
                # Hledej část cesty která končí "_internal/"
                idx = m.filename.find("_internal/")
                if idx >= 0:
                    prefix = m.filename[:idx + len("_internal/")]
                    break
            print(f"  Stripping prefix: '{prefix}'")
            for i, member in enumerate(members, 1):
                if not member.filename.startswith(prefix):
                    continue
                # Ořízni prefix — zbytek je relativní cesta uvnitř _internal
                member.filename = member.filename[len(prefix):]
                if not member.filename:
                    continue
                zf.extract(member, old_internal)
                if i % 200 == 0 or i == total:
                    print(f"  {i}/{total} files extracted...")
        print(f"  OK — {total} files")
        return True
    except Exception as e:
        print(f"  ERROR extracting: {e}")
        return False


def main():
    print(f"SOFTWARE_ROOT: {SOFTWARE_ROOT}\n")

    if not SOFTWARE_ROOT.exists():
        print("ERROR: SOFTWARE_ROOT does not exist or is not accessible.")
        input("Press Enter to exit.")
        return

    zip_path = find_zip(SOFTWARE_ROOT)
    if zip_path is None:
        print("ERROR: No .zip file found in SOFTWARE_ROOT.")
        input("Press Enter to exit.")
        return

    print(f"ZIP file: {zip_path.name}")

    # Zkontroluj které složky existují
    existing = []
    missing = []
    for name in TARGET_DIRS:
        p = SOFTWARE_ROOT / name
        if p.exists() and p.is_dir():
            existing.append(p)
        else:
            missing.append(name)

    print(f"\nTarget folders found: {len(existing)}/{len(TARGET_DIRS)}")
    if missing:
        print(f"Missing (will be skipped): {missing}")

    print(f"\nThis will extract '{zip_path.name}' into {len(existing)} folders.")
    print("Existing _internal folders will be archived with a timestamp suffix.")
    confirm = input("\nProceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    print()
    ok = 0
    fail = 0
    for dst in existing:
        print(f"→ {dst.name}")
        success = extract_to(zip_path, dst)
        if success:
            ok += 1
        else:
            fail += 1

    print(f"\n{'='*40}")
    print(f"DONE  ✓ {ok} OK  |  ✗ {fail} failed")
    print(f"{'='*40}")
    input("\nPress Enter to exit.")


if __name__ == "__main__":
    main()