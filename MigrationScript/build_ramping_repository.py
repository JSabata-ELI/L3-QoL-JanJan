from pathlib import Path
import pandas as pd
import json
import re

# ============================================================
# Nastavení
# ============================================================

SOURCE_DIR = Path(r"./xlsx_rampings")
TARGET_DIR = Path(r"./RampingRepository")

KEEP_COLUMNS = [
    "waveplate",
    "ptm1",
    "pcm2",
    "pcm4",
    "pap1",
    "sbw4",
]

# ============================================================

TARGET_DIR.mkdir(exist_ok=True)

index = {
    "rampings": []
}

xlsx_files = sorted(SOURCE_DIR.glob("*.xlsx"))

seen_files = set()

for file in xlsx_files:

    print(f"Processing {file.name}")

    try:
        df = pd.read_excel(file)

    except Exception as e:
        print(f"ERROR: {file.name}: {e}")
        continue

    missing = [
        c for c in KEEP_COLUMNS
        if c not in df.columns
    ]

    if missing:
        print(
            f"SKIPPED {file.name} "
            f"(missing columns {missing})"
        )
        continue

    # pouze požadované sloupce
    df = df[KEEP_COLUMNS]

    parquet_name = file.stem + ".parquet"

    if parquet_name in seen_files:
        print(f"SKIPPED duplicate: {parquet_name}")
        continue

    seen_files.add(parquet_name)

    parquet_path = TARGET_DIR / parquet_name

    try:
        df.to_parquet(
            parquet_path,
            index=False
        )

    except Exception as e:
        print(f"ERROR writing parquet: {e}")
        continue

    # --------------------------------------------------------
    # timestamp z názvu souboru
    # ramping_yyyy_mm_dd-hh_mm.xlsx
    # --------------------------------------------------------

    timestamp = ""

    m = re.search(
        r"(\d{4})_(\d{2})_(\d{2})-(\d{2})_(\d{2})",
        file.stem
    )

    if m:
        timestamp = (
            f"{m.group(1)}-"
            f"{m.group(2)}-"
            f"{m.group(3)} "
            f"{m.group(4)}:"
            f"{m.group(5)}"
        )

    index["rampings"].append(
        {
            "file": parquet_name,
            "timestamp": timestamp,
            "rows": int(len(df)),
            "columns": KEEP_COLUMNS,
            "source_xlsx": file.name,
        }
    )

# ============================================================
# seřadit podle timestampu
# ============================================================

index["rampings"].sort(
    key=lambda r: r.get("timestamp", ""),
    reverse=True
)

# ============================================================
# uložit index
# ============================================================

with open(
    TARGET_DIR / "index.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        index,
        f,
        indent=2,
        ensure_ascii=False
    )

print()
print("Finished.")
print(f"Converted {len(index['rampings'])} rampings.")
print(f"Repository: {TARGET_DIR}")