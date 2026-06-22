from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt



APP_DIR = Path(__file__).parent
DATA_REPOSITORY = APP_DIR.parent / "DataRepository"
FEATURE_DIR = APP_DIR / "Features"

FEATURE_DIR.mkdir(exist_ok=True)

PLOT_DIR = APP_DIR / "Plots"
PLOT_DIR.mkdir(exist_ok=True)




SEGMENT_DIR = APP_DIR / "Segments"
SEGMENT_DIR.mkdir(exist_ok=True)

OPERATION_DIR = APP_DIR / "Operations"
OPERATION_DIR.mkdir(exist_ok=True)

MASTER_OPERATIONS_FILE = APP_DIR / "MasterOperations.parquet"

REQUIRED_COLUMNS = [
    "timestamp",
    "waveplate",
    "ptm1",
    "pcm2",
    "pcm4",
    "pap1",
    "sbw4",
]

def rebuild_master_operations():
    files = sorted(OPERATION_DIR.glob("*_operations.parquet"))

    if not files:
        print("No operation files found.")
        return

    tables = []

    for file in files:
        df = pd.read_parquet(file)

        if not df.empty:
            tables.append(df)

    if not tables:
        print("No operation segments found.")
        return

    master = pd.concat(
        tables,
        ignore_index=True
    )

    master = master.sort_values(
        ["date", "start_time"]
    )

    master.to_parquet(
        MASTER_OPERATIONS_FILE,
        index=False
    )

    print(
    f"Master operations rows: {len(master)}"
    )

    print(
        master["waveplate"]
        .value_counts()
        .head(10)
    )

    print(f"Saved {MASTER_OPERATIONS_FILE}")

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

       
    valid_sbw4_green = (
        df[["sbw4", "pcm2", "pcm4"]]
        .notna()
        .all(axis=1)
    )

    valid_green_ptm1 = (
        df[["pcm2", "pcm4", "ptm1"]]
        .notna()
        .all(axis=1)
    )

    valid_ptm1_pap1 = (
        df[["ptm1", "pap1"]]
        .notna()
        .all(axis=1)
    )
    
    valid_pcm2_green = (
        df[["pcm2", "pcm4"]]
        .notna()
        .all(axis=1)
    )

    valid_sbw4_ptm1 = (
        df[["sbw4", "ptm1"]]
        .notna()
        .all(axis=1)
    )
   
    df["sbw4_green"] = np.where(
        valid_sbw4_green,
        safe_divide(
            df["sbw4"],
            df["green"]
        ),
        np.nan
    )

    df["green_ptm1"] = np.where(
        valid_green_ptm1,
        safe_divide(
            df["green"],
            df["ptm1"]
        ),
        np.nan
    )

    df["ptm1_pap1"] = np.where(
        valid_ptm1_pap1,
        safe_divide(
            df["ptm1"],
            df["pap1"]
        ),
        np.nan
    )

    df["pcm2_green"] = np.where(
        valid_pcm2_green,
        safe_divide(
            df["pcm2"],
            df["green"]
        ),
        np.nan
    )

    df["sbw4_ptm1"] = np.where(
        valid_sbw4_ptm1,
        safe_divide(
            df["sbw4"],
            df["ptm1"]
        ),
        np.nan
    )    

    return df


def iter_daily_parquets():
    for file in sorted(DATA_REPOSITORY.glob("*/*.parquet")):
        yield file


def process_file(file):

    df = pd.read_parquet(file)

    df = add_shot_features(df)

    save_features(df, file)

    segmented = build_waveplate_segments(df)

    summary = summarize_segments(segmented)

    summary["date"] = pd.to_datetime(file.stem)

    save_segments(summary, file)

    operations = get_operation_segments(summary)

    operations["operation_id"] = (
        operations["date"].dt.strftime("%Y%m%d")
        + "_"
        + operations["segment_id"].astype(str)
    )

    save_operations(operations, file)

    print_segment_overview(summary)

    print_operation_segments(summary)
    
    plot_day_segments(summary, file.stem)

def build_waveplate_segments(df):
    df = df.copy()

    df["segment_id"] = (
        df["waveplate"]
        .ne(df["waveplate"].shift())
        .cumsum()
    )

    return df

def save_operations(operations, file):
    out_file = OPERATION_DIR / f"{file.stem}_operations.parquet"

    operations.to_parquet(
        out_file,
        index=False
    )

    print(f"Saved {out_file}")

def plot_day_segments(summary, day_name):

    summary = summary.sort_values("waveplate")

    plt.figure(figsize=(10, 6))

    plt.errorbar(
        summary["waveplate"],
        summary["sbw4_ptm1_median"],
        yerr=summary["sbw4_ptm1_mad"],
        fmt="o-"
    )

    plt.xlabel("Waveplate")
    plt.ylabel("SBW4/PTM1")

    plt.title(day_name)

    plt.grid(True)

    plt.tight_layout()

    out_file = PLOT_DIR / f"{day_name}_sbw4_ptm1.png"

    plt.savefig(
        out_file,
        dpi=150
    )

    plt.close()

    print(f"Saved {out_file}")

def save_features(df, file):
    out_file = FEATURE_DIR / f"{file.stem}_features.parquet"

    df.to_parquet(
        out_file,
        index=False
    )

    print(f"Saved {out_file}")

def summarize_segments(df):
    grouped = df.groupby("segment_id")

    summary = grouped.agg(
        start_time=("timestamp", "min"),
        end_time=("timestamp", "max"),

        waveplate=("waveplate", "first"),
        waveplate_min=("waveplate", "min"),
        waveplate_max=("waveplate", "max"),

        ptm1_median=("ptm1", "median"),
        sbw4_median=("sbw4", "median"),
        green_median=("green", "median"),
        pap1_median=("pap1", "median"),

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


    summary["segment_type"] = (
        summary.apply(classify_segment, axis=1)
    )

    return summary.reset_index()

def classify_segment(row):

    if row["shots"] >= 10:
        return "operation"

    if row["shots"] >= 3:
        return "hold"

    return "ramping"




def save_segments(summary, file):
    out_file = SEGMENT_DIR / f"{file.stem}_segments.parquet"

    summary.to_parquet(
        out_file,
        index=False
    )

    print(f"Saved {out_file}")


def print_operation_segments(summary):

    operation_segments = summary[
        summary["segment_type"] == "operation"
    ]

    if len(operation_segments) == 0:
        return

    print("\nOperation segments:")

    print(
        operation_segments[
            [
                "waveplate",
                "shots",
                "duration_s",
                "sbw4_ptm1_median",
            ]
        ]
    )

def print_segment_overview(summary):
    print(
        "Segments:",
        len(summary),
        "| shots median:",
        summary["shots"].median(),
        "| shots min/max:",
        summary["shots"].min(),
        summary["shots"].max(),
    )
    print(
    summary["segment_type"]
    .value_counts()
)

def mad(series):
    med = series.median()
    return (series - med).abs().median()


def get_operation_segments(summary):
    return summary[
        summary["segment_type"] == "operation"
    ].copy()

def needs_processing(file):
    segment_file = SEGMENT_DIR / f"{file.stem}_segments.parquet"
    operation_file = OPERATION_DIR / f"{file.stem}_operations.parquet"

    return not segment_file.exists() or not operation_file.exists()

def main():

    files = list(iter_daily_parquets())

    if not files:
        print(f"No parquet files found in {DATA_REPOSITORY}")
        return

    for file in files:

        if not needs_processing(file):
            print(f"Skipping {file.stem}, already processed")
            continue

        print(f"Processing {file}")
        process_file(file)

    rebuild_master_operations()

if __name__ == "__main__":
    main()