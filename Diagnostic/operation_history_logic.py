from pathlib import Path
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

PRIMARY_METRICS = [
    "sbw4_green_median",
    "green_ptm1_median",
    "ptm1_pap1_median",
    "pcm2_green_median",
]
SECONDARY_METRICS = ["sbw4_ptm1_median"]


def _mad(series):
    med = series.median()
    return (series - med).abs().median()


def _filter_valid_metric_data(df, metric):
    df = df.copy()
    df[metric] = pd.to_numeric(df[metric], errors="coerce")
    df = df[df[metric].notna()]

    if metric in ("sbw4_green_median", "green_ptm1_median", "pcm2_green_median"):
        df["green_median"] = pd.to_numeric(df["green_median"], errors="coerce")
        df = df[df["green_median"].notna() & (df["green_median"] > 0)]

    return df


class OperationHistoryLogic:

    def __init__(self, app_dir):
        self.app_dir = Path(app_dir)
        self.master_file = self.app_dir / "MasterOperations.parquet"
        self.plot_dir = self.app_dir / "HistoryPlots"
        self.plot_dir.mkdir(exist_ok=True)

    def load_master(self):
        if not self.master_file.exists():
            raise FileNotFoundError(f"MasterOperations.parquet not found: {self.master_file}")
        df = pd.read_parquet(self.master_file)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def _summarize_waveplate(self, df, waveplate, log_fn):
        wp = df[df["waveplate"] == waveplate].copy()
        if len(wp) < 5:
            return None

        metrics = PRIMARY_METRICS + SECONDARY_METRICS
        log_fn(f"\n=== Waveplate {waveplate} ===")

        for metric in metrics:
            metric_df = _filter_valid_metric_data(wp, metric)
            if len(metric_df) < 5:
                continue

            latest_date = metric_df["date"].max()
            wp30 = metric_df[metric_df["date"] >= latest_date - pd.Timedelta(days=30)]
            baseline_all = metric_df[metric].median()
            baseline_30d = wp30[metric].median()
            if baseline_30d == 0 or pd.isna(baseline_30d):
                continue

            latest = metric_df.sort_values("date").iloc[-1][metric]
            deviation_pct = 100 * (latest - baseline_30d) / baseline_30d

            log_fn(
                f"  {metric:25s}"
                f" 30d={baseline_30d:.6f}"
                f" all={baseline_all:.6f}"
                f" latest={latest:.6f}"
                f" deviation={deviation_pct:+.2f}%"
            )

            if abs(deviation_pct) > 3:
                log_fn(f"  WARNING: {metric} deviation={deviation_pct:+.2f}%")

        return wp

    def _plot_metric_history(self, wp_df, waveplate, metric):
        metric_df = _filter_valid_metric_data(wp_df, metric)
        if metric_df.empty:
            return

        fig = Figure(figsize=(10, 5))
        ax = fig.add_subplot(111)
        ax.plot(metric_df["date"], metric_df[metric], marker="o")
        baseline = metric_df[metric].median()
        ax.axhline(baseline, linestyle="--", color="gray")
        ax.set_title(f"{metric} @ WP={waveplate}")
        ax.set_xlabel("Date")
        ax.set_ylabel(metric)
        ax.grid(True)
        fig.tight_layout()

        outfile = self.plot_dir / f"{int(waveplate)}_{metric}.png"
        fig.savefig(outfile, dpi=150)
        return outfile

    def run(self, log_fn=print, stop_flag=None):
        df = self.load_master()
        log_fn(f"Master operations rows: {len(df)}")

        common_waveplates = df["waveplate"].value_counts().head(25).index
        saved_plots = []

        for waveplate in common_waveplates:
            if stop_flag and stop_flag():
                log_fn("Stopped by user.")
                break

            wp_df = self._summarize_waveplate(df, waveplate, log_fn)
            if wp_df is None:
                continue

            for metric in PRIMARY_METRICS:
                try:
                    outfile = self._plot_metric_history(wp_df, waveplate, metric)
                    if outfile:
                        saved_plots.append(outfile)
                        log_fn(f"Saved {outfile}")
                except Exception as e:
                    log_fn(f"Plot error {metric} WP={waveplate}: {e}")

        log_fn(f"\nHistory analysis finished. {len(saved_plots)} plots saved to {self.plot_dir}")
