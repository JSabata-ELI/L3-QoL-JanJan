from pathlib import Path
import pandas as pd
import numpy as np

DETECTORS = ["sbw4", "ptm1", "pap1"]
CONTROL_COLUMNS = ["waveplate", "ln36"]
LOW_SBW4_THRESHOLD = 0.5
REFERENCE_WINDOW = 5
STABILITY_LIMITS = {
    "waveplate": 0.2,
    "ptm1": 0.2,
    "pap1": 0.2,
    "pcm2": 0.2,
    "pcm4": 0.2,
}


def _equal(a, b):
    if pd.isna(a) and pd.isna(b):
        return True
    return a == b


def _changed(a, b):
    if pd.isna(a) and pd.isna(b):
        return False
    return a != b


def _relative_change(a, b):
    if pd.isna(a) or pd.isna(b):
        return np.inf
    if b == 0:
        return np.inf
    return abs(a - b) / abs(b)


def _median_reference(df, current_index, column, window=REFERENCE_WINDOW):
    start = max(0, current_index - window)
    values = df.iloc[start:current_index][column].dropna()
    if len(values) == 0:
        return np.nan
    return values.median()


def inspect_file(file_path):
    try:
        df = pd.read_parquet(file_path)
    except Exception:
        return []

    required = ["timestamp", "sbw4", "ptm1", "pap1", "waveplate", "ln36", "pcm2"]
    for col in required:
        if col not in df.columns:
            return []

    suspicious = []

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]

        detectors_same = all(_equal(curr[col], prev[col]) for col in DETECTORS)
        controls_changed = any(_changed(curr[col], prev[col]) for col in CONTROL_COLUMNS)

        if detectors_same and controls_changed:
            suspicious.append((i, curr["timestamp"], Path(file_path).name, "DETECTORS_UNCHANGED"))

        pcm2 = curr["pcm2"]
        if pd.notna(pcm2) and pcm2 == 0:
            suspicious.append((i, curr["timestamp"], Path(file_path).name, "PCM2_ZERO"))

        sbw4 = curr["sbw4"]
        ln36 = curr["ln36"]
        if pd.notna(sbw4) and sbw4 < LOW_SBW4_THRESHOLD and pd.notna(ln36) and ln36 == 1:
            stable = True
            for column, limit in STABILITY_LIMITS.items():
                if column not in df.columns:
                    stable = False
                    break
                reference = _median_reference(df, i, column)
                if pd.isna(reference):
                    stable = False
                    break
                change = _relative_change(curr[column], reference)
                if change > limit:
                    stable = False
                    break
            if stable:
                suspicious.append((i, curr["timestamp"], Path(file_path).name,
                                   f"SBW4_LOW_OUTLIER sbw4={sbw4:.3f}"))

    return suspicious


def run_detection(repository_dir, log_fn=print):
    files = sorted(Path(repository_dir).rglob("*.parquet"))
    all_suspicious = []

    for file in files:
        found = inspect_file(file)
        all_suspicious.extend(found)

    log_fn(f"Total suspicious rows: {len(all_suspicious)}")
    return all_suspicious
