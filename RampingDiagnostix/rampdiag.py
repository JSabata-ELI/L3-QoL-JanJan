from pathlib import Path
import numpy as np
import pandas as pd


APP_DIR = Path(__file__).parent
DATA_REPOSITORY = APP_DIR.parent / "DataRepository"
FEATURE_DIR = APP_DIR / "Features"

FEATURE_DIR.mkdir(exist_ok=True)

SEGMENT_DIR = APP_DIR / "Segments"
SEGMENT_DIR.mkdir(exist_ok=True)

REQUIRED_COLUMNS = [
    "timestamp",
    "waveplate",
    "ptm1",
    "pcm2",
    "pcm4",
    "pap1",
    "sbw4",
]


def safe_divide(a, b):
    return np.where(
        (b != 0) & np.isfinite(b),
        a / b,
        np.nan,
    )


def add_shot_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ns", errors="coerce")

    numeric_cols = [
        "waveplate",
        "ptm1",
        "pcm2",
        "pcm4",
        "pap1",
        "sbw4",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["green"] = df["pcm2"] + df["pcm4"]

    df["sbw4_ptm1"] = safe_divide(df["sbw4"], df["ptm1"])
    df["sbw4_green"] = safe_divide(df["sbw4"], df["green"])
    df["green_ptm1"] = safe_divide(df["green"], df["ptm1"])
    df["ptm1_pap1"] = safe_divide(df["ptm1"], df["pap1"])
    df["pcm2_green"] = safe_divide(df["pcm2"], df["green"])

    return df


def iter_daily_parquets():
    for file in sorted(DATA_REPOSITORY.glob("*/*.parquet")):
        yield file


def process_day_file(file: Path):
    df = pd.read_parquet(file)
    df = add_shot_features(df)

    out_file = FEATURE_DIR / f"{file.stem}_features.parquet"
    df.to_parquet(out_file, index=False)

    print(f"Saved {out_file}")


def build_waveplate_segments(df):
    df = df.copy()

    df["segment_id"] = (
        df["waveplate"]
        .ne(df["waveplate"].shift())
        .cumsum()
    )

    return df

def summarize_segments(df):
    grouped = df.groupby("segment_id")

    summary = grouped.agg(
        start_time=("timestamp", "min"),
        end_time=("timestamp", "max"),

        waveplate=("waveplate", "median"),

        shots=("waveplate", "size"),

        sbw4_ptm1_median=("sbw4_ptm1", "median"),
        sbw4_green_median=("sbw4_green", "median"),
        green_ptm1_median=("green_ptm1", "median"),
        ptm1_pap1_median=("ptm1_pap1", "median"),
        pcm2_green_median=("pcm2_green", "median"),

        sbw4_ptm1_mad=("sbw4_ptm1", mad),
        sbw4_green_mad=("sbw4_green", mad),
        green_ptm1_mad=("green_ptm1", mad),
        ptm1_pap1_mad=("ptm1_pap1", mad),
        pcm2_green_mad=("pcm2_green", mad),

        duration_s=(
            "timestamp",
            lambda x: (x.max() - x.min()).total_seconds()
        )

    )


    return summary.reset_index()

def mad(series):
    med = series.median()
    return (series - med).abs().median()

def process_day_segments(file):
    df = pd.read_parquet(file)

    df = add_shot_features(df)

    df = build_waveplate_segments(df)

    summary = summarize_segments(df)

    out_file = SEGMENT_DIR / f"{file.stem}_segments.parquet"

    summary.to_parquet(
        out_file,
        index=False
    )

    print(f"Saved {out_file}")

def main():
    for file in files:

        print(f"Processing {file}")

        process_day_file(file)

        process_day_segments(file)

    if not files:
        print(f"No parquet files found in {DATA_REPOSITORY}")
        return

    for file in files:
        print(f"Processing {file}")
        process_day_file(file)


if __name__ == "__main__":
    main()