# Diagnostic

PySide6 GUI for monitoring and analysis of L3 laser beamline operations. Fetches data from the CPVA archive, builds a daily parquet repository, and processes it into operation segments, ratio trends, anomaly flags, and long-term drift plots.

---

## Quick Overview

| Tab | What it does |
|-----|--------------|
| **Builder** | Fetches PV data from CPVA API day-by-day into `DataRepository/` |
| **Browser** | Inspect and edit any parquet file in the repository |
| **Suspicious** | Scans files for anomalous rows (frozen detectors, PCM2=0, SBW4 outliers) |
| **Diagnostix** | Segments shots by waveplate position, computes energy ratios, saves plots |
| **Filter Plot** | Interactive violin / scatter plots with per-variable filters and statistics |
| **History** | Detects >3 % deviation from 30-day baseline per waveplate operation |

---

## Detailed Description

### Data flow

```
CPVA API (8 PVs)
  └─ builder_logic.py ──► DataRepository/{year}/{YYYY-MM-DD}.parquet
                                │
                ┌───────────────┴──────────────┐
                │                              │
        rampdiag_logic.py            detect_suspicious_logic.py
                │                              │
   Features/  Segments/  Operations/      anomaly table (GUI only)
                │
        MasterOperations.parquet
                │
        operation_history_logic.py
                │
        HistoryPlots/{waveplate}_{metric}.png
```

---

### Module descriptions

#### `builder_logic.py` — Repository Builder
Fetches raw PV samples from the CPVA REST API (`https://10.78.0.57:8443`) and writes one parquet file per day to `DataRepository/`.

Key steps applied to each day's data:
- **Chunked fetch** — 1-hour chunks, up to 12 parallel workers; night hours 22:00–06:00 (Prague) are skipped.
- **Sample-hold merge** — events within a 137 ms window are collapsed into a single row, propagating the last known value for each PV.
- **Row filtering pipeline**:
  - Remove rows where only the master PV (waveplate) changed without a real shot.
  - Keep only rows where master PV is a multiple of `master_multiple` (1000).
  - Remove spurious hourly-boundary artefacts.
  - Apply energy conditions: PTM1 in 5–150 mJ/cm², PAP1 in 0.021–1.0.
  - Remove rows where ln36 changed alone.
  - Remove rows with detectors frozen while controls changed, or PCM2 = 0.
- Progress and last processed day are stored in `builder_state.json`.

Configuration is read from `builder_config.json` (master PV, monitored PVs, start date, conditions).

---

#### `rampdiag_logic.py` — Ramping Diagnostix
Processes daily feature files into operation segments and energy-ratio summaries.

1. **Derived features** added per shot:
   - `green = PCM2 + PCM4`
   - Ratios: `sbw4_green`, `green_ptm1`, `ptm1_pap1`, `pcm2_green`, `sbw4_ptm1`

2. **Waveplate segmentation** — consecutive shots at the same waveplate position are grouped. Segments are classified by shot count:
   - `operation` ≥ 10 shots
   - `hold` 3–9 shots
   - `ramping` < 3 shots

3. **Segment summary** — per segment: start/end time, duration, waveplate value, medians and MAD for each ratio, shot count.

4. **Output files** (written under the Diagnostic folder):
   - `Features/{date}_features.parquet`
   - `Segments/{date}_segments.parquet`
   - `Operations/{date}_operations.parquet` (operation segments only)
   - `Plots/{date}_sbw4_ptm1.png` (error-bar plot)
   - `MasterOperations.parquet` — concatenation of all daily operations files.

---

#### `detect_suspicious_logic.py` — Anomaly Detection
Scans every row of a parquet file and flags three types of anomalies:

| Flag | Condition |
|------|-----------|
| `DETECTORS_UNCHANGED` | sbw4 / ptm1 / pap1 identical to previous row while waveplate or ln36 changed |
| `PCM2_ZERO` | PCM2 energy = 0 |
| `SBW4_LOW_OUTLIER` | SBW4 < 0.5 mJ/cm², ln36 = 1, all values within 20 % of a 5-sample rolling median |

Results are displayed in the **Suspicious** tab as a filterable table (file, row index, timestamp, reason).

---

#### `operation_history_logic.py` — Operation History
Reads `MasterOperations.parquet` and analyses long-term drift per waveplate position.

- Metrics tracked: `sbw4_green`, `green_ptm1`, `ptm1_pap1`, `pcm2_green`, `sbw4_ptm1`
- Baseline: 30-day rolling median.
- Deviation threshold: > 3 % flags the point.
- Output: PNG line plots saved to `HistoryPlots/{waveplate}_{metric}.png` for the 25 most common waveplates.

---

### Configuration files

**`builder_config.json`**
```
master_pv          waveplate position feedback (L3-PFWP6-MTR03-1:RawPos)
master_multiple    1000
start_date         first day to process
pvs                dict of label → CPVA channel name for 8 PVs
conditions         per-PV min/max filters applied after merging
```

**`builder_state.json`**
```
last_processed_day  YYYY-MM-DD — builder resumes from the next day
```

---

### Dependencies

```
PySide6      GUI framework
pandas       data manipulation
numpy        numerical operations
matplotlib   plots
requests     CPVA API calls
urllib3      HTTP (SSL verification disabled for internal API)
pyarrow      parquet I/O (via pandas)
```
