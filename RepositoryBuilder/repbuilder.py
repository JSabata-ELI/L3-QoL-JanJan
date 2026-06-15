from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import requests
import urllib3
import pandas as pd
import pyarrow
import urllib.parse
from zoneinfo import ZoneInfo
from copy import copy

urllib3.disable_warnings(
    urllib3.exceptions.InsecureRequestWarning
)

APP_DIR = Path(__file__).parent

CONFIG_FILE = APP_DIR / "builder_config.json"
print("CONFIG:", CONFIG_FILE)
STATE_FILE = APP_DIR / "builder_state.json"

REPOSITORY_DIR = APP_DIR.parent / "DataRepository"

REPOSITORY_DIR.mkdir(exist_ok=True)

CPVA_BASE_URL = "https://10.78.0.57:8443/api/1.0/cpva"

CPVA_SAMPLES_ENDPOINT = "/samples"
CPVA_HTTP_TIMEOUT = 10.0
CHUNK_SIZE_NS = int(3600 * 1e9)

SAMPLE_HOLD_MIN_GAP_MS = 137

print(CONFIG_FILE)
print(CONFIG_FILE.exists())

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    txt = f.read()

print(repr(txt))

CONFIG = json.loads(txt)

with open(CONFIG_FILE, "r") as f:
    CONFIG = json.load(f)

def load_state():

    if STATE_FILE.exists():

        with open(STATE_FILE, "r") as f:
            return json.load(f)

    return {
        "last_processed_day": None
    }


_SESSION = requests.Session()
_SESSION.verify = False


def _http_get_json(url, timeout=30):

    r = _SESSION.get(
        url,
        timeout=timeout
    )

    r.raise_for_status()

    return r.json()



def save_state(state):

    with open(STATE_FILE, "w") as f:
        json.dump(
            state,
            f,
            indent=2
        )

def cpva_fetch_samples(channel: str, start_ns: int, end_ns: int,
                       timeout: float = CPVA_HTTP_TIMEOUT) -> list[dict]:
    params = urllib.parse.urlencode({
        "channelName": channel,
        "start": str(start_ns),
        "end":   str(end_ns),
    })
    url  = f"{CPVA_BASE_URL}{CPVA_SAMPLES_ENDPOINT}?{params}"
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, list):
        raise ValueError(f"Unexpected response shape: {type(data).__name__}")
    return data

def _chunk_is_night(chunk_start_ns: int, chunk_end_ns: int) -> bool:
    """Return True if the entire chunk is within 22:00-06:00 Prague time (no data expected)."""
    TZ = ZoneInfo("Europe/Prague")
    now_ns_val = int(datetime.now(timezone.utc).timestamp() * 1e9)
    # Never skip chunks that extend to current time or future
    if chunk_end_ns >= now_ns_val - 60 * 1_000_000_000:  # within 1 min of now
        return False
    dt_start = datetime.fromtimestamp(chunk_start_ns / 1e9, tz=TZ)
    dt_end   = datetime.fromtimestamp(chunk_end_ns   / 1e9, tz=TZ)
    def is_night(h): return h >= 22 or h < 6
    return is_night(dt_start.hour) and is_night(dt_end.hour)

def cpva_fetch_samples_chunked(channel: str, start_ns: int, end_ns: int,
                               timeout: float = CPVA_HTTP_TIMEOUT,
                               log_fn=None,
                               max_workers: int = 12) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    chunks = []
    cs = start_ns
    i = 0

    while cs < end_ns:
        ce = min(cs + CHUNK_SIZE_NS, end_ns)
        if not _chunk_is_night(cs, ce):
            chunks.append((i, cs, ce))
        i += 1
        cs = ce

    if not chunks:
        return []

    if len(chunks) == 1:
        return cpva_fetch_samples(channel, chunks[0][1], chunks[0][2], timeout)

    if log_fn:
        log_fn(f"      {channel}: {len(chunks)} chunks")

    workers = min(max_workers, len(chunks))
    results_map = {}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(cpva_fetch_samples, channel, cs, ce, timeout): idx
            for idx, cs, ce in chunks
        }

        for fut in as_completed(futures):
            idx = futures[fut]
            results_map[idx] = fut.result()

    results = []
    for idx in sorted(results_map):
        results.extend(results_map[idx])

    return results

def cpva_decode_value(sample: dict):
    val = sample.get("value")

    if val is None:
        return None

    if isinstance(val, (int, float, str)):
        return val

    if isinstance(val, list):
        if len(val) == 1:
            return val[0]

        # ASCII decode zkoušej jen pro kratší listy
        if len(val) <= 512 and val and all(isinstance(x, int) and 0 <= x < 128 for x in val):
            try:
                decoded = "".join(map(chr, val))
                if decoded.strip():
                    return decoded
            except Exception:
                pass

        return val

    return val

def merge_samples_sample_hold(samples_by_pv: dict, pv_order: list[str]) -> list:
    """
    Group detector updates that belong to one shot.

    Logic:
    - all PV events are sorted by timestamp
    - events closer than SAMPLE_HOLD_MIN_GAP_MS belong to the same shot
    - row timestamp is the LAST timestamp in that shot window
    - row values are sample&hold values after applying all events in the shot
    """

    events = []

    for pv_name in pv_order:
        for ts_ns, value, units in samples_by_pv.get(pv_name, []):
            events.append((ts_ns, pv_name, value, units))

    if not events:
        return []

    events.sort(key=lambda e: e[0])

    merge_gap_ns = SAMPLE_HOLD_MIN_GAP_MS * 1_000_000

    rows = []
    last_values = {}

    group_start_ts = None
    group_last_ts = None
    group_events = []

    def flush_group():
        nonlocal group_events, group_last_ts

        if not group_events:
            return

        for _ts_ns, pv_name, value, units in group_events:
            last_values[pv_name] = (value, units)

        rows.append((group_last_ts, copy(last_values)))

    for ts_ns, pv_name, value, units in events:
        if group_start_ts is None:
            group_start_ts = ts_ns
            group_last_ts = ts_ns
            group_events = [(ts_ns, pv_name, value, units)]
            continue

        if ts_ns - group_last_ts <= merge_gap_ns:
            group_last_ts = ts_ns
            group_events.append((ts_ns, pv_name, value, units))
        else:
            flush_group()

            group_start_ts = ts_ns
            group_last_ts = ts_ns
            group_events = [(ts_ns, pv_name, value, units)]

    flush_group()

    return rows

def remove_master_only_rows(
    rows: list,
    master_pv: str
) -> list:
    """
    Remove rows where only the selected master PV changed
    and all other PV values stayed identical.

    This removes fake rows caused only by ramp/master PV updates.
    """

    if not rows or not master_pv:
        return rows

    filtered = [rows[0]]

    for ts_ns, row_dict in rows[1:]:
        _prev_ts, prev_row = filtered[-1]

        keys = set(prev_row.keys()) | set(row_dict.keys())

        same_other_values = True

        for pv in keys:
            if pv == master_pv:
                continue

            if prev_row.get(pv) != row_dict.get(pv):
                same_other_values = False
                break

        master_changed = prev_row.get(master_pv) != row_dict.get(master_pv)

        if master_changed and same_other_values:
            # Skip this row: only master PV changed.
            continue

        filtered.append((ts_ns, row_dict))

    return filtered

def remove_fake_hour_boundary_rows(rows,master_pv: list) -> list:
    """
    Remove fake rows caused by CPVA chunk boundaries.

    If two consecutive rows are separated by ~1 hour
    (3599-3601 s) and at least 2 PV values are identical,
    the newer row is considered fake and removed.
    """

    if len(rows) < 2:
        return rows

    filtered = [rows[0]]

    MIN_DIFF_NS = int(3599 * 1e9)
    MAX_DIFF_NS = int(3601 * 1e9)

    for ts_ns, row_dict in rows[1:]:

        prev_ts, prev_row = filtered[-1]

        dt_ns = ts_ns - prev_ts

        # only inspect ~1h gaps
        if MIN_DIFF_NS <= dt_ns <= MAX_DIFF_NS:

            same_count = 0

            shared_pvs = set(prev_row.keys()) & set(row_dict.keys())

            for pv in shared_pvs:

                if pv == master_pv:
                    continue

                prev_val = prev_row[pv][0]
                curr_val = row_dict[pv][0]

                if prev_val == curr_val:
                    same_count += 1

                if same_count >= 2:
                    break

            if same_count >= 2:
                # fake row detected -> skip it
                continue

        filtered.append((ts_ns, row_dict))

    return filtered

def filter_master_multiple_rows(
    rows: list,
    master_pv: str,
    multiple: float
) -> list:
    """
    Keep only rows where master PV is a multiple of selected number.
    Empty value = no filtering.
    """
    multiple = float(multiple)

    if not rows or not multiple:
        return rows

    try:
        multiple = float(multiple)
    except ValueError:
        return rows

    if multiple <= 0:
        return rows

    filtered = []
    tolerance = max(1e-6, abs(multiple) * 1e-9)

    for ts_ns, row_dict in rows:
        # Důležité: pokud master PV v řádku není, řádek nemá jak ověřit násobek.
        # Proto ho smažeme.
        if master_pv not in row_dict:
            continue

        value, _units = row_dict[master_pv]

        if not isinstance(value, (int, float)):
            continue

        nearest_multiple = round(value / multiple) * multiple

        if abs(value - nearest_multiple) <= tolerance:
            filtered.append((ts_ns, row_dict))

    return filtered


def apply_conditions(rows, conditions):

    if not conditions:
        return rows

    filtered = []

    for ts_ns, row_dict in rows:

        ok = True

        for cond in conditions:

            pv = cond["pv"]

            if pv not in row_dict:
                ok = False
                break

            value, _units = row_dict[pv]

            if not isinstance(value, (int, float)):
                ok = False
                break

            vmin = cond.get("min")
            vmax = cond.get("max")

            if vmin is not None and value < vmin:
                ok = False
                break

            if vmax is not None and value > vmax:
                ok = False
                break

        if ok:
            filtered.append((ts_ns, row_dict))

    return filtered

def process_day(day):

    start_dt = day.replace(
        hour=5,
        minute=0,
        second=0,
        microsecond=0
    )

    end_dt = day.replace(
        hour=23,
        minute=0,
        second=0,
        microsecond=0
    )

    start_ns = int(start_dt.timestamp() * 1e9)
    end_ns = int(end_dt.timestamp() * 1e9)

    samples_by_pv = {}

    for alias, pv in CONFIG["pvs"].items():

        raw = cpva_fetch_samples_chunked(
            pv,
            start_ns,
            end_ns
        )

        parsed = []

        for s in raw:

            ts_ns = s.get("time")

            if ts_ns is None:
                continue

            value = cpva_decode_value(s)

            if not isinstance(value, (int, float)):
                continue

            parsed.append(
                (
                    int(ts_ns),
                    float(value),
                    ""
                )
            )

        samples_by_pv[pv] = parsed

    rows = merge_samples_sample_hold(
        samples_by_pv,
        list(CONFIG["pvs"].values())
    )

    rows = remove_master_only_rows(
        rows,
        CONFIG["master_pv"]
    )

    rows = filter_master_multiple_rows(
        rows,
        CONFIG["master_pv"],
        CONFIG["master_multiple"]
    )

    rows = remove_fake_hour_boundary_rows(
        rows,
        CONFIG["master_pv"]
    )


    rows = apply_conditions(
        rows,
        CONFIG.get("conditions", [])
    )


    print(
        f"Downloading {day:%Y-%m-%d}"
    )

    print(
        f"{day:%Y-%m-%d} "
        f"rows={len(rows)}"
    )

    if not rows:
        print(f"{day:%Y-%m-%d} rows=0 -> skipped")
        return

    saved_file = save_day(rows, day)
    print(f"Saved {saved_file}")


def save_day(rows, day):

    data = []

    for ts_ns, row_dict in rows:

        row = {
            "timestamp": ts_ns
        }

        for alias, pv_name in CONFIG["pvs"].items():

            if pv_name in row_dict:

                value, _units = row_dict[pv_name]

                row[alias] = value

        data.append(row)

    df = pd.DataFrame(data)

    if df.empty:
        print(f"Skipping {day:%Y-%m-%d} (0 rows)")
        return None

    year_dir = REPOSITORY_DIR / str(day.year)

    year_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    target_file = (
        year_dir
        / f"{day:%Y-%m-%d}.parquet"
    )


    for col in df.columns:
        if col != "timestamp":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df.to_parquet(
        target_file,
        index=False
    )

    return target_file

state = load_state()

if state["last_processed_day"]:

    start_day = (
        datetime.strptime(
            state["last_processed_day"],
            "%Y-%m-%d"
        )
        + timedelta(days=1)
    )

else:

    start_day = datetime(
        2026,
        1,
        1
    )

today = datetime.now()

current_day = start_day

while current_day.date() < today.date():

    process_day(current_day)

    state["last_processed_day"] = (
        current_day.strftime("%Y-%m-%d")
    )

    save_state(state)

    current_day += timedelta(days=1)