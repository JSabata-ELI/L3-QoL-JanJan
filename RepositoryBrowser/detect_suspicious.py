from pathlib import Path

import pandas as pd
import numpy as np


REPOSITORY_DIR = (
    Path(__file__).resolve().parent.parent
    / "DataRepository"
)


DETECTORS = [
    "sbw4",
    "ptm1",
    "pap1",
]


CONTROL_COLUMNS = [
    "waveplate",
    "ln36",
]

LOW_SBW4_THRESHOLD = 0.5

REFERENCE_WINDOW = 5

STABILITY_LIMITS = {
    "waveplate": 0.02,
    "ptm1": 0.05,
    "pap1": 0.05,
    "pcm2": 0.05,
    "pcm4": 0.05,
}

STABILITY_LIMITS = {
    "waveplate": 0.2,   # 20 %
    "ptm1": 0.2,        # 20 %
    "pap1": 0.2,
    "pcm2": 0.2,
    "pcm4": 0.2,
}


def equal(a, b):

    if pd.isna(a) and pd.isna(b):
        return True

    return a == b


def changed(a, b):

    if pd.isna(a) and pd.isna(b):
        return False

    return a != b


def relative_change(a, b):

    if pd.isna(a) or pd.isna(b):
        return np.inf

    if b == 0:
        return np.inf

    return abs(a - b) / abs(b)


def median_reference(
    df,
    current_index,
    column,
    window=REFERENCE_WINDOW
):

    start = max(0, current_index - window)

    values = (
        df.iloc[start:current_index][column]
        .dropna()
    )

    if len(values) == 0:
        return np.nan

    return values.median()


def inspect_file(file_path):

    try:
        df = pd.read_parquet(file_path)

    except Exception as e:

        print(
            f"ERROR {file_path}: {e}"
        )

        return []

    required = [
        "timestamp",
        "sbw4",
        "ptm1",
        "pap1",
        "waveplate",
        "ln36",
        "pcm2",
    ]

    for col in required:

        if col not in df.columns:

            print(
                f"SKIP {file_path.name}: missing column {col}"
            )

            return []

    suspicious = []

    for i in range(1, len(df)):

        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        # ====================================
        # Rule A
        # ====================================

        detectors_same = all(
            equal(
                curr[col],
                prev[col]
            )
            for col in DETECTORS
        )

        controls_changed = any(
            changed(
                curr[col],
                prev[col]
            )
            for col in CONTROL_COLUMNS
        )

        if detectors_same and controls_changed:

            suspicious.append(
                (
                    i,
                    curr["timestamp"],
                    "DETECTORS_UNCHANGED"
                )
            )

        # ====================================
        # Rule B
        # ====================================

        pcm2 = curr["pcm2"]

        if (
            pd.notna(pcm2)
            and pcm2 == 0
        ):

            suspicious.append(
                (
                    i,
                    curr["timestamp"],
                    "PCM2_ZERO"
                )
            )

        # ====================================
        # Rule C
        # ====================================

        sbw4 = curr["sbw4"]
        ln36 = curr["ln36"]

        if (
            pd.notna(sbw4)
            and sbw4 < LOW_SBW4_THRESHOLD
            and pd.notna(ln36)
            and ln36 == 1
        ):

            stable = True

            for column, limit in STABILITY_LIMITS.items():

                reference = median_reference(
                    df,
                    i,
                    column
                )

                if pd.isna(reference):
                    stable = False
                    break

                change = relative_change(
                    curr[column],
                    reference
                )

                if change > limit:
                    stable = False
                    break

            if stable:

                suspicious.append(
                    (
                        i,
                        curr["timestamp"],
                        f"SBW4_LOW_OUTLIER sbw4={sbw4:.3f}"
                    )
                )

    return suspicious


def main():

    files = sorted(
        REPOSITORY_DIR.rglob("*.parquet")
    )

    total = 0

    for file in files:

        suspicious = inspect_file(file)

        if not suspicious:
            continue

        print()
        print("=" * 80)
        print(file)
        print("=" * 80)

        for row_idx, timestamp, reason in suspicious:

            print(
                f"row={row_idx:<8d}"
                f" timestamp={timestamp}"
                f" reason={reason}"
            )

        total += len(suspicious)

    print()
    print(
        f"TOTAL SUSPICIOUS ROWS: {total}"
    )


if __name__ == "__main__":
    main()