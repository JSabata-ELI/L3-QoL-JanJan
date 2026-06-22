from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


APP_DIR = Path(__file__).parent

MASTER_FILE = APP_DIR / "MasterOperations.parquet"

PLOT_DIR = APP_DIR / "HistoryPlots"
PLOT_DIR.mkdir(exist_ok=True)

PRIMARY_METRICS = [
    "sbw4_green_median",
    "green_ptm1_median",
    "ptm1_pap1_median",
    "pcm2_green_median",
]

SECONDARY_METRICS = [
    "sbw4_ptm1_median",
]

def mad(series):
    med = series.median()
    return (series - med).abs().median()


def load_master():

    if not MASTER_FILE.exists():
        raise FileNotFoundError(MASTER_FILE)

    df = pd.read_parquet(MASTER_FILE)

    df["date"] = pd.to_datetime(df["date"])

    return df


def print_overview(df):

    print("\n==============================")
    print("MASTER OVERVIEW")
    print("==============================")

    print(f"Rows: {len(df)}")

    print("\nMost common waveplates:")

    print(
        df["waveplate"]
        .value_counts()
        .head(20)
    )


def summarize_waveplate(df, waveplate):

    wp = df[
        df["waveplate"] == waveplate
    ].copy()

    if len(wp) < 5:
        print(
            f"Waveplate {waveplate}: only {len(wp)} entries"
        )
        return None

    metrics = (
        PRIMARY_METRICS
        + SECONDARY_METRICS
    )

    print("\n===================================")
    print(f"WAVEPLATE {waveplate}")
    print("===================================")

    for metric in metrics:

        metric_df = filter_valid_metric_data(
            wp,
            metric
        )

        if len(metric_df) < 5:
            continue        

        latest_date = metric_df["date"].max()

        wp30 = metric_df[
            metric_df["date"]
            >= latest_date - pd.Timedelta(days=30)
        ]

        baseline_all = metric_df[metric].median()

        baseline_30d = wp30[metric].median()
        if baseline_30d == 0 or pd.isna(baseline_30d):
            continue

        latest = (
            metric_df
            .sort_values("date")
            .iloc[-1][metric]
        )

        deviation_pct = (
            100
            * (latest - baseline_30d)
            / baseline_30d
        )

        print(
            f"{metric:25s}"
            f" 30d={baseline_30d:.6f}"
            f" all={baseline_all:.6f}"
            f" latest={latest:.6f}"
            f" deviation={deviation_pct:+.2f}%"
        )

    return wp


def plot_metric_history(
    wp_df,
    waveplate,
    metric
):

    wp_df = wp_df.copy()

    wp_df[metric] = pd.to_numeric(
        wp_df[metric],
        errors="coerce"
    )

    wp_df = filter_valid_metric_data(wp_df, metric) 

    if wp_df.empty:
        print(f"No valid data for {metric} @ WP={waveplate}")
        return

    plt.figure(figsize=(10, 5))

    plt.plot(
        wp_df["date"],
        wp_df[metric],
        marker="o"
    )

    

    baseline = wp_df[metric].median()

    plt.axhline(
        baseline,
        linestyle="--"
    )

    plt.title(
        f"{metric} @ WP={waveplate}"
    )

    plt.xlabel("Date")
    plt.ylabel(metric)

    plt.grid(True)

    plt.tight_layout()

    outfile = (
        PLOT_DIR
        / f"{int(waveplate)}_{metric}.png"
    )

    plt.savefig(
        outfile,
        dpi=150
    )

    plt.close()

    print(f"Saved {outfile}")


def filter_valid_metric_data(df, metric):

    df = df.copy()

    df[metric] = pd.to_numeric(
        df[metric],
        errors="coerce"
    )

    df = df[
        df[metric].notna()
    ]

    if metric in [
        "sbw4_green_median",
        "green_ptm1_median",
        "pcm2_green_median",
    ]:

        df["green_median"] = pd.to_numeric(
            df["green_median"],
            errors="coerce"
        )

        df = df[
            df["green_median"].notna()
            & (df["green_median"] > 0)
        ]

    return df

def detect_simple_anomalies(
    wp_df,
    waveplate
):

    metrics = PRIMARY_METRICS

    print("\nANOMALY CHECK")

    for metric in metrics:

        metric_df = filter_valid_metric_data(
            wp_df,
            metric
        )

        if len(metric_df) < 5:
            continue

        latest_date = metric_df["date"].max()

        wp30 = metric_df[
            metric_df["date"]
            >= latest_date - pd.Timedelta(days=30)
        ]

        latest_row = (
            metric_df
            .sort_values("date")
            .iloc[-1]
        )

        baseline_all = metric_df[metric].median()

        baseline_30d = wp30[metric].median()

        if baseline_30d == 0 or pd.isna(baseline_30d):
            continue

        latest = latest_row[metric]

        deviation_pct = (
            100
            * (latest - baseline_30d)
            / baseline_30d
        )

        if abs(deviation_pct) > 3:

            print(
                f"WARNING: {metric}"
                f" deviation={deviation_pct:+.2f}%"
            )


def main():

    df = load_master()

    print_overview(df)

    common_waveplates = (
        df["waveplate"]
        .value_counts()
        .head(25)
        .index
    )

    for waveplate in common_waveplates:

        wp_df = summarize_waveplate(
            df,
            waveplate
        )

        if wp_df is None:
            continue

        detect_simple_anomalies(
            wp_df,
            waveplate
        )

        for metric in PRIMARY_METRICS:

            plot_metric_history(
                wp_df,
                waveplate,
                metric
            )


if __name__ == "__main__":
    main()