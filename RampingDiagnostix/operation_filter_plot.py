from pathlib import Path
import tkinter as tk
from tkinter import ttk

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


APP_DIR = Path(__file__).parent
DATA_REPOSITORY = APP_DIR.parent / "DataRepository"

PLOT_DIR = APP_DIR / "FilteredPlots"
PLOT_DIR.mkdir(exist_ok=True)


AVAILABLE_COLUMNS = [
    "waveplate",
    "ptm1",
    "pcm2",
    "pcm4",
    "pap1",
    "sbw4",
    "green",

    "sbw4_green",
    "green_ptm1",
    "ptm1_pap1",
    "pcm2_green",
    "sbw4_ptm1",
]


def safe_divide(a, b):
    return np.where(
        a.notna()
        & b.notna()
        & (b != 0)
        & np.isfinite(b),
        a / b,
        np.nan,
    )


def add_shot_features(df):
    df = df.copy()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ns",
        errors="coerce"
    )

    numeric_cols = [
        "waveplate",
        "ptm1",
        "pcm2",
        "pcm4",
        "pap1",
        "sbw4",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    df["green"] = df["pcm2"] + df["pcm4"]

    valid_sbw4_green = df[
        ["sbw4", "pcm2", "pcm4"]
    ].notna().all(axis=1)

    valid_green_ptm1 = df[
        ["pcm2", "pcm4", "ptm1"]
    ].notna().all(axis=1)

    valid_ptm1_pap1 = df[
        ["ptm1", "pap1"]
    ].notna().all(axis=1)

    valid_pcm2_green = df[
        ["pcm2", "pcm4"]
    ].notna().all(axis=1)

    valid_sbw4_ptm1 = df[
        ["sbw4", "ptm1"]
    ].notna().all(axis=1)

    df["sbw4_green"] = np.where(
        valid_sbw4_green,
        safe_divide(df["sbw4"], df["green"]),
        np.nan
    )

    df["green_ptm1"] = np.where(
        valid_green_ptm1,
        safe_divide(df["green"], df["ptm1"]),
        np.nan
    )

    df["ptm1_pap1"] = np.where(
        valid_ptm1_pap1,
        safe_divide(df["ptm1"], df["pap1"]),
        np.nan
    )

    df["pcm2_green"] = np.where(
        valid_pcm2_green,
        safe_divide(df["pcm2"], df["green"]),
        np.nan
    )

    df["sbw4_ptm1"] = np.where(
        valid_sbw4_ptm1,
        safe_divide(df["sbw4"], df["ptm1"]),
        np.nan
    )

    return df


def iter_daily_parquets():
    for file in sorted(DATA_REPOSITORY.glob("*/*.parquet")):
        yield file


def load_repository():
    tables = []

    for file in iter_daily_parquets():
        try:
            df = pd.read_parquet(file)
            df = add_shot_features(df)
            df["source_file"] = file.name
            tables.append(df)
        except Exception as exc:
            print(f"Skipping {file}: {exc}")

    if not tables:
        raise RuntimeError(f"No parquet data loaded from {DATA_REPOSITORY}")

    df = pd.concat(
        tables,
        ignore_index=True
    )

    return df


def apply_conditions(df, conditions):
    filtered = df.copy()
    ranges = []

    for condition in conditions:
        variable = condition["variable"]
        target = condition["target"]
        tolerance_percent = condition["tolerance_percent"]

        filtered[variable] = pd.to_numeric(
            filtered[variable],
            errors="coerce"
        )

        lower = target * (1 - tolerance_percent / 100)
        upper = target * (1 + tolerance_percent / 100)

        filtered = filtered[
            filtered[variable].between(
                lower,
                upper,
                inclusive="both"
            )
        ]

        ranges.append(
            (variable, lower, upper)
        )

    return filtered, ranges

def plot_daily_statistics(
    filtered,
    y_variable,
    condition_text,
):

    filtered = filtered.copy()

    filtered[y_variable] = pd.to_numeric(
        filtered[y_variable],
        errors="coerce"
    )

    filtered = filtered.dropna(
        subset=[
            "timestamp",
            y_variable,
        ]
    )

    if filtered.empty:
        print("No valid data for daily statistics.")
        return

    filtered["day"] = (
        filtered["timestamp"]
        .dt.floor("D")
    )

    days = sorted(
        filtered["day"].unique()
    )

    data_by_day = []

    valid_days = []

    for day in days:

        values = (
            filtered.loc[
                filtered["day"] == day,
                y_variable
            ]
            .dropna()
            .values
        )

        if len(values) < 2:
            continue

        data_by_day.append(values)
        valid_days.append(day)

    if not data_by_day:
        print("No days with enough points for daily histograms.")
        return

    positions = list(range(len(valid_days)))

    plt.figure(figsize=(14, 7))

    parts = plt.violinplot(
        data_by_day,
        positions=positions,
        showmeans=False,
        showmedians=True,
        showextrema=True,
        widths=0.7,
    )

    plt.xticks(
        positions,
        [
            pd.to_datetime(day).strftime("%Y-%m-%d")
            for day in valid_days
        ],
        rotation=45,
        ha="right",
    )

    plt.xlabel("Date")
    plt.ylabel(y_variable)

    plt.title(
        f"{y_variable} daily distribution | {condition_text}"
    )

    plt.grid(True, axis="y")
    plt.tight_layout()

    out_file = (
        PLOT_DIR
        / f"{y_variable}_daily_distribution.png"
    )

    plt.savefig(
        out_file,
        dpi=150
    )

    plt.show()

    print(f"Saved {out_file}")
    print(f"Days plotted: {len(valid_days)}")

def plot_filtered_history(
    df,
    y_variable,
    conditions,
):
    df = df.copy()

    df[y_variable] = pd.to_numeric(
        df[y_variable],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "timestamp",
            y_variable,
        ]
    )

    filtered, ranges = apply_conditions(
        df,
        conditions
    )

    filtered = filtered.dropna(
        subset=[
            "timestamp",
            y_variable,
        ]
    )

    if filtered.empty:
        print("No data after filtering.")
        return

    filtered = filtered.sort_values("timestamp")

    plt.figure(figsize=(12, 6))

    plt.scatter(
        filtered["timestamp"],
        filtered[y_variable],
        s=20,
    )

    plt.xlabel("Time")
    plt.ylabel(y_variable)

    condition_text = ", ".join(
        f"{var}={low:.3g}–{high:.3g}"
        for var, low, high in ranges
    )

    plt.title(
        f"{y_variable} over time | {condition_text}"
    )

    plt.grid(True)
    plt.tight_layout()

    safe_condition = "_".join(
        f"{var}_{low:.3g}_{high:.3g}"
        for var, low, high in ranges
    ).replace("/", "_")

    out_file = (
        PLOT_DIR
        / f"{y_variable}_filtered_{safe_condition}.png"
    )

    plt.savefig(
        out_file,
        dpi=150
    )

    plt.show()

    print(f"Filtered rows: {len(filtered)}")
    print(f"Saved {out_file}")

def launch_gui():
    root = tk.Tk()
    root.title("Shot Data Filter Plot")

    root.geometry("900x500")

    data_frame = ttk.LabelFrame(
        root,
        text="Data Selection"
    )

    data_frame.grid(
        row=0,
        column=0,
        sticky="ew",
        padx=10,
        pady=5
    )

    condition_frame = ttk.LabelFrame(
        root,
        text="Conditions"
    )

    condition_frame.grid(
        row=1,
        column=0,
        sticky="ew",
        padx=10,
        pady=5
    )

    action_frame = ttk.LabelFrame(
        root,
        text="Actions"
    )

    action_frame.grid(
        row=2,
        column=0,
        sticky="ew",
        padx=10,
        pady=5
    )

    stats_frame = ttk.LabelFrame(
        root,
        text="Statistics"
    )

    stats_frame.grid(
        row=3,
        column=0,
        sticky="ew",
        padx=10,
        pady=5
    )

    df_cache = {
        "df": None
    }

    stats_text = tk.StringVar()

    stats_label = tk.Label(
        stats_frame,
        textvariable=stats_text,
        justify="left"
    )

    stats_label.pack(
        anchor="w",
        padx=10,
        pady=5
    )    

    tk.Label(
        data_frame,
        text="Y Variable"
    ).grid(row=0, column=0, padx=5, pady=5)

    tk.Label(
        data_frame,
        text="From"
    ).grid(
        row=1,
        column=0
    )

    from_entry = tk.Entry(
        data_frame,
        width=15
    )

    from_entry.insert(
        0,
        "2026-01-01"
    )

    from_entry.grid(
        row=1,
        column=1
    )

    tk.Label(
        data_frame,
        text="To"
    ).grid(
        row=1,
        column=2
    )

    to_entry = tk.Entry(
        data_frame,
        width=15
    )

    to_entry.insert(
        0,
        pd.Timestamp.now().strftime("%Y-%m-%d")
    )

    to_entry.grid(
        row=1,
        column=3
    )

    y_var = tk.StringVar(
        value="sbw4"
    )

    plot_mode = tk.StringVar(
        value="daily"
    )

    ttk.Combobox(
        data_frame,
        textvariable=y_var,
        values=AVAILABLE_COLUMNS,
        width=30
    ).grid(row=0, column=1, padx=5, pady=5)


    tk.Label(
        data_frame,
        text="Plot Mode"
    ).grid(
        row=0,
        column=2,
        padx=10
    )

    plot_mode = tk.StringVar(
        value="Daily distribution"
    )

    ttk.Combobox(
        data_frame,
        textvariable=plot_mode,
        values=[
            "Raw shots",
            "Daily distribution"
        ],
        width=20
    ).grid(
        row=0,
        column=3,
        padx=10
    )


    condition_rows = []

  
    condition_frame.grid(
        row=1,
        column=0,
        columnspan=4,
        padx=5,
        pady=5
    )

    def add_condition(
        default_variable="green",
        default_target="75",
        default_tolerance="5"
    ):
        row = len(condition_rows)

        var = tk.StringVar(
            value=default_variable
        )

        enabled = tk.BooleanVar(
            value=True
        )

        tk.Checkbutton(
            condition_frame,
            variable=enabled
        ).grid(row=row, column=0)

        ttk.Combobox(
            condition_frame,
            textvariable=var,
            values=AVAILABLE_COLUMNS,
            width=25
        ).grid(row=row, column=1, padx=5, pady=2)

        target_entry = tk.Entry(
            condition_frame,
            width=12
        )
        target_entry.insert(0, default_target)
        target_entry.grid(row=row, column=2, padx=5, pady=2)

        tol_entry = tk.Entry(
            condition_frame,
            width=12
        )
        tol_entry.insert(0, default_tolerance)
        tol_entry.grid(row=row, column=3, padx=5, pady=2)

        remove_button = tk.Button(
            condition_frame,
            text="X",
            width=3
        )

        remove_button.grid(
            row=row,
            column=4
        )

        condition_rows.append(
            {
                "enabled": enabled,
                "variable": var,
                "target": target_entry,
                "tolerance": tol_entry,
            }
        )

    tk.Label(
        condition_frame,
        text="Condition variable"
    ).grid(row=0, column=0)

    tk.Label(
        condition_frame,
        text="Target"
    ).grid(row=0, column=1)

    tk.Label(
        condition_frame,
        text="Tolerance [%]"
    ).grid(row=0, column=2)

    def add_condition_button():
        add_condition(
            default_variable="green",
            default_target="75",
            default_tolerance="5"
        )

    def do_plot():
        if df_cache["df"] is None:
            print("Loading DataRepository...")
            df_cache["df"] = load_repository()
            print(f"Loaded rows: {len(df_cache['df'])}")

        conditions = []

        for row in condition_rows:
            if not row["enabled"].get():
                continue
            conditions.append(
                {
                    "variable": row["variable"].get(),
                    "target": float(row["target"].get()),
                    "tolerance_percent": float(row["tolerance"].get()),
                }
            )

        df = df_cache["df"]

        from_date = pd.to_datetime(
            from_entry.get()
        )

        to_date = pd.to_datetime(
            to_entry.get()
        )

        df = df[
            (df["timestamp"] >= from_date)
            &
            (df["timestamp"] <= to_date)
        ]

        filtered, ranges = apply_conditions(
            df,
            conditions
        )

        condition_text = ", ".join(
            f"{var}={low:.3g}-{high:.3g}"
            for var, low, high in ranges
        )

        if plot_mode.get() == "Raw shots":

            plot_filtered_history(
                df,
                y_var.get(),
                conditions,
            )

        else:

            plot_daily_statistics(
                filtered,
                y_var.get(),
                condition_text,
            )
        
        stats_text.set(
            f"""
        Points: {len(filtered)}

        Median:
        {filtered[y_var.get()].median():.3f}

        Std:
        {filtered[y_var.get()].std():.3f}

        Min:
        {filtered[y_var.get()].min():.3f}

        Max:
        {filtered[y_var.get()].max():.3f}
        """
        )

    add_condition_button()

    tk.Button(
        root,
        text="Add condition",
        command=add_condition_button
    ).grid(row=2, column=0, padx=5, pady=10)

    tk.Button(
        root,
        text="Plot",
        command=do_plot
    ).grid(row=2, column=1, padx=5, pady=10)

    root.mainloop()


def main():
    launch_gui()


if __name__ == "__main__":
    main()