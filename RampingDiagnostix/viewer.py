from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
APP_DIR = Path(__file__).parent

file = APP_DIR / "Segments" / "2026-06-05_segments.parquet"

print(file)
print(file.exists())

df = pd.read_parquet(file)

print(df.head())
print(df.columns.tolist())
print(df.shape)
print(
    df[[
        "waveplate",
        "shots",
        "duration_s",
        "sbw4_ptm1_median",
        "sbw4_ptm1_mad"
    ]]
    .head(30)
)
print(
    df[
        [
            "start_time",
            "waveplate"
        ]
    ].head(200)
)


plt.plot(df["waveplate"])
plt.show()