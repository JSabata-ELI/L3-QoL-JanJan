from pathlib import Path
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

REQUIRED_COLUMNS = ["timestamp", "waveplate", "ptm1", "pcm2", "pcm4", "pap1", "sbw4"]


def _mad(series):
    med = series.median()
    return (series - med).abs().median()


def _safe_divide(a, b):
    return np.where((b != 0) & np.isfinite(b), a / b, np.nan)


class RampingDiagnostixLogic:

    def __init__(self, data_repository, app_dir):
        self.data_repository = Path(data_repository)
        self.app_dir = Path(app_dir)
        self.feature_dir = self.app_dir / "Features"
        self.segment_dir = self.app_dir / "Segments"
        self.operation_dir = self.app_dir / "Operations"
        self.plot_dir = self.app_dir / "Plots"
        self.master_file = self.app_dir / "MasterOperations.parquet"

        for d in [self.feature_dir, self.segment_dir, self.operation_dir, self.plot_dir]:
            d.mkdir(exist_ok=True)

    def add_shot_features(self, df):
        df = df.copy()
        missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing:
            raise ValueError(f"Missing columns: {missing}")

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ns", errors="coerce")

        for col in ["waveplate", "ptm1", "pcm2", "pcm4", "pap1", "sbw4"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["green"] = df["pcm2"] + df["pcm4"]

        v_sbw4_green = df[["sbw4", "pcm2", "pcm4"]].notna().all(axis=1)
        v_green_ptm1 = df[["pcm2", "pcm4", "ptm1"]].notna().all(axis=1)
        v_ptm1_pap1 = df[["ptm1", "pap1"]].notna().all(axis=1)
        v_pcm2_green = df[["pcm2", "pcm4"]].notna().all(axis=1)
        v_sbw4_ptm1 = df[["sbw4", "ptm1"]].notna().all(axis=1)

        df["sbw4_green"] = np.where(v_sbw4_green, _safe_divide(df["sbw4"], df["green"]), np.nan)
        df["green_ptm1"] = np.where(v_green_ptm1, _safe_divide(df["green"], df["ptm1"]), np.nan)
        df["ptm1_pap1"] = np.where(v_ptm1_pap1, _safe_divide(df["ptm1"], df["pap1"]), np.nan)
        df["pcm2_green"] = np.where(v_pcm2_green, _safe_divide(df["pcm2"], df["green"]), np.nan)
        df["sbw4_ptm1"] = np.where(v_sbw4_ptm1, _safe_divide(df["sbw4"], df["ptm1"]), np.nan)

        return df

    def build_waveplate_segments(self, df):
        df = df.copy()
        df["segment_id"] = df["waveplate"].ne(df["waveplate"].shift()).cumsum()
        return df

    def _classify_segment(self, row):
        if row["shots"] >= 10:
            return "operation"
        if row["shots"] >= 3:
            return "hold"
        return "ramping"

    def summarize_segments(self, df):
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
            sbw4_ptm1_mad=("sbw4_ptm1", _mad),
            sbw4_green_mad=("sbw4_green", _mad),
            green_ptm1_mad=("green_ptm1", _mad),
            ptm1_pap1_mad=("ptm1_pap1", _mad),
            pcm2_green_mad=("pcm2_green", _mad),
            duration_s=("timestamp", lambda x: (x.max() - x.min()).total_seconds()),
        )
        summary["segment_type"] = summary.apply(self._classify_segment, axis=1)
        return summary.reset_index()

    def needs_processing(self, file):
        seg = self.segment_dir / f"{file.stem}_segments.parquet"
        ops = self.operation_dir / f"{file.stem}_operations.parquet"
        return not seg.exists() or not ops.exists()

    def process_file(self, file, log_fn=print):
        df = pd.read_parquet(file)
        df = self.add_shot_features(df)

        feat_file = self.feature_dir / f"{file.stem}_features.parquet"
        df.to_parquet(feat_file, index=False)

        segmented = self.build_waveplate_segments(df)
        summary = self.summarize_segments(segmented)
        summary["date"] = pd.to_datetime(file.stem)

        seg_file = self.segment_dir / f"{file.stem}_segments.parquet"
        summary.to_parquet(seg_file, index=False)

        operations = summary[summary["segment_type"] == "operation"].copy()
        operations["operation_id"] = (
            operations["date"].dt.strftime("%Y%m%d")
            + "_"
            + operations["segment_id"].astype(str)
        )
        ops_file = self.operation_dir / f"{file.stem}_operations.parquet"
        operations.to_parquet(ops_file, index=False)

        log_fn(f"  {file.stem}: {len(summary)} segments, {len(operations)} operations")

        try:
            self._plot_day_segments(summary, file.stem)
        except Exception as e:
            log_fn(f"  Plot error: {e}")

    def _plot_day_segments(self, summary, day_name):
        s = summary.sort_values("waveplate")
        fig = Figure(figsize=(10, 6))
        ax = fig.add_subplot(111)
        ax.errorbar(s["waveplate"], s["sbw4_ptm1_median"], yerr=s["sbw4_ptm1_mad"], fmt="o-")
        ax.set_xlabel("Waveplate")
        ax.set_ylabel("SBW4/PTM1")
        ax.set_title(day_name)
        ax.grid(True)
        fig.tight_layout()
        out_file = self.plot_dir / f"{day_name}_sbw4_ptm1.png"
        fig.savefig(out_file, dpi=150)

    def rebuild_master_operations(self, log_fn=print):
        files = sorted(self.operation_dir.glob("*_operations.parquet"))
        if not files:
            log_fn("No operation files found.")
            return

        tables = []
        for f in files:
            try:
                df = pd.read_parquet(f)
                if not df.empty:
                    tables.append(df)
            except Exception:
                pass

        if not tables:
            log_fn("No operation segments found.")
            return

        master = pd.concat(tables, ignore_index=True)
        master = master.sort_values(["date", "start_time"])
        master.to_parquet(self.master_file, index=False)
        log_fn(f"Master operations: {len(master)} rows → {self.master_file}")

    def run(self, log_fn=print, stop_flag=None):
        files = sorted(self.data_repository.glob("*/*.parquet"))
        if not files:
            log_fn(f"No parquet files found in {self.data_repository}")
            return

        log_fn(f"Found {len(files)} files.")
        for file in files:
            if stop_flag and stop_flag():
                log_fn("Stopped by user.")
                break
            if not self.needs_processing(file):
                log_fn(f"Skipping {file.stem} (already processed)")
                continue
            log_fn(f"Processing {file.stem}...")
            try:
                self.process_file(file, log_fn)
            except Exception as e:
                log_fn(f"ERROR {file.stem}: {e}")

        self.rebuild_master_operations(log_fn)
        log_fn("Diagnostix finished.")
