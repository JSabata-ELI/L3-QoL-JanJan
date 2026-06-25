from pathlib import Path
from datetime import datetime, timedelta, timezone
import json
import urllib.parse
from zoneinfo import ZoneInfo
from copy import copy
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CPVA_BASE_URL = "https://10.78.0.57:8443/api/1.0/cpva"
CPVA_SAMPLES_ENDPOINT = "/samples"
CPVA_HTTP_TIMEOUT = 10.0
CHUNK_SIZE_NS = int(3600 * 1e9)
SAMPLE_HOLD_MIN_GAP_MS = 137


class RepositoryBuilderLogic:

    def __init__(self, config, state_file, repository_dir):
        self.config = config
        self.state_file = Path(state_file)
        self.repository_dir = Path(repository_dir)
        self.repository_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.verify = False

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    def load_state(self):
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                return json.load(f)
        return {"last_processed_day": None}

    def save_state(self, state):
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    # ------------------------------------------------------------------
    # CPVA fetching
    # ------------------------------------------------------------------

    def _http_get_json(self, url, timeout=30):
        r = self._session.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def cpva_fetch_samples(self, channel, start_ns, end_ns, timeout=CPVA_HTTP_TIMEOUT):
        params = urllib.parse.urlencode({
            "channelName": channel,
            "start": str(start_ns),
            "end": str(end_ns),
        })
        url = f"{CPVA_BASE_URL}{CPVA_SAMPLES_ENDPOINT}?{params}"
        data = self._http_get_json(url, timeout=timeout)
        if not isinstance(data, list):
            raise ValueError(f"Unexpected response: {type(data).__name__}")
        return data

    def _chunk_is_night(self, chunk_start_ns, chunk_end_ns):
        TZ = ZoneInfo("Europe/Prague")
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        if chunk_end_ns >= now_ns - 60 * 1_000_000_000:
            return False
        dt_start = datetime.fromtimestamp(chunk_start_ns / 1e9, tz=TZ)
        dt_end = datetime.fromtimestamp(chunk_end_ns / 1e9, tz=TZ)
        def is_night(h):
            return h >= 22 or h < 6
        return is_night(dt_start.hour) and is_night(dt_end.hour)

    def cpva_fetch_samples_chunked(self, channel, start_ns, end_ns,
                                    timeout=CPVA_HTTP_TIMEOUT, log_fn=None, max_workers=12):
        chunks = []
        cs, i = start_ns, 0
        while cs < end_ns:
            ce = min(cs + CHUNK_SIZE_NS, end_ns)
            if not self._chunk_is_night(cs, ce):
                chunks.append((i, cs, ce))
            i += 1
            cs = ce

        if not chunks:
            return []
        if len(chunks) == 1:
            return self.cpva_fetch_samples(channel, chunks[0][1], chunks[0][2], timeout)

        if log_fn:
            log_fn(f"  {channel}: {len(chunks)} chunks")

        results_map = {}
        with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as ex:
            futures = {
                ex.submit(self.cpva_fetch_samples, channel, cs, ce, timeout): idx
                for idx, cs, ce in chunks
            }
            for fut in as_completed(futures):
                results_map[futures[fut]] = fut.result()

        result = []
        for idx in sorted(results_map):
            result.extend(results_map[idx])
        return result

    def cpva_decode_value(self, sample):
        val = sample.get("value")
        if val is None:
            return None
        if isinstance(val, (int, float, str)):
            return val
        if isinstance(val, list):
            if len(val) == 1:
                return val[0]
            if len(val) <= 512 and all(isinstance(x, int) and 0 <= x < 128 for x in val):
                try:
                    decoded = "".join(map(chr, val))
                    if decoded.strip():
                        return decoded
                except Exception:
                    pass
            return val
        return val

    # ------------------------------------------------------------------
    # Row processing
    # ------------------------------------------------------------------

    def merge_samples_sample_hold(self, samples_by_pv, pv_order):
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
        group_start_ts = group_last_ts = None
        group_events = []

        def flush():
            if not group_events:
                return
            for _ts, pv, val, units in group_events:
                last_values[pv] = (val, units)
            rows.append((group_last_ts, copy(last_values)))

        for ts_ns, pv_name, value, units in events:
            if group_start_ts is None:
                group_start_ts = group_last_ts = ts_ns
                group_events = [(ts_ns, pv_name, value, units)]
                continue
            if ts_ns - group_last_ts <= merge_gap_ns:
                group_last_ts = ts_ns
                group_events.append((ts_ns, pv_name, value, units))
            else:
                flush()
                group_start_ts = group_last_ts = ts_ns
                group_events = [(ts_ns, pv_name, value, units)]

        flush()
        return rows

    def _values_equal(self, a, b):
        if a is None and b is None:
            return True
        try:
            if pd.isna(a) and pd.isna(b):
                return True
        except Exception:
            pass
        return a == b

    def _get_alias_value(self, row_dict, alias):
        pv_name = self.config["pvs"].get(alias)
        if pv_name is None or pv_name not in row_dict:
            return None
        value, _units = row_dict[pv_name]
        return value

    def remove_master_only_rows(self, rows, master_pv):
        if not rows or not master_pv:
            return rows
        filtered = [rows[0]]
        for ts_ns, row_dict in rows[1:]:
            _, prev_row = filtered[-1]
            keys = set(prev_row.keys()) | set(row_dict.keys())
            same_others = all(
                prev_row.get(pv) == row_dict.get(pv)
                for pv in keys if pv != master_pv
            )
            if prev_row.get(master_pv) != row_dict.get(master_pv) and same_others:
                continue
            filtered.append((ts_ns, row_dict))
        return filtered

    def remove_fake_hour_boundary_rows(self, rows, master_pv):
        if len(rows) < 2:
            return rows
        filtered = [rows[0]]
        MIN_NS = int(3599 * 1e9)
        MAX_NS = int(3601 * 1e9)
        for ts_ns, row_dict in rows[1:]:
            prev_ts, prev_row = filtered[-1]
            dt_ns = ts_ns - prev_ts
            if MIN_NS <= dt_ns <= MAX_NS:
                same_count = sum(
                    1 for pv in set(prev_row.keys()) & set(row_dict.keys())
                    if pv != master_pv and prev_row[pv][0] == row_dict[pv][0]
                )
                if same_count >= 2:
                    continue
            filtered.append((ts_ns, row_dict))
        return filtered

    def filter_master_multiple_rows(self, rows, master_pv, multiple):
        multiple = float(multiple)
        if not rows or multiple <= 0:
            return rows
        tolerance = max(1e-6, abs(multiple) * 1e-9)
        filtered = []
        for ts_ns, row_dict in rows:
            if master_pv not in row_dict:
                continue
            value, _ = row_dict[master_pv]
            if not isinstance(value, (int, float)):
                continue
            if abs(value - round(value / multiple) * multiple) <= tolerance:
                filtered.append((ts_ns, row_dict))
        return filtered

    def remove_ln36_only_rows(self, rows, ln36_pv="ln36"):
        if len(rows) < 2:
            return rows
        filtered = [rows[0]]
        for ts_ns, row_dict in rows[1:]:
            _, prev_row = filtered[-1]
            keys = set(prev_row.keys()) | set(row_dict.keys())
            only_ln36 = all(
                prev_row.get(pv) == row_dict.get(pv)
                for pv in keys if pv != ln36_pv
            )
            if only_ln36:
                continue
            filtered.append((ts_ns, row_dict))
        return filtered

    def apply_conditions(self, rows, conditions):
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
                value, _ = row_dict[pv]
                if not isinstance(value, (int, float)):
                    ok = False
                    break
                if cond.get("min") is not None and value < cond["min"]:
                    ok = False
                    break
                if cond.get("max") is not None and value > cond["max"]:
                    ok = False
                    break
            if ok:
                filtered.append((ts_ns, row_dict))
        return filtered

    def remove_suspicious_rows(self, rows):
        detectors = ["sbw4", "ptm1", "pap1"]
        controls = ["waveplate", "ln36"]
        if len(rows) < 2:
            return rows
        filtered = [rows[0]]
        removed = 0
        for ts_ns, row_dict in rows[1:]:
            _, prev_row = filtered[-1]
            detectors_same = all(
                self._values_equal(
                    self._get_alias_value(row_dict, a),
                    self._get_alias_value(prev_row, a)
                )
                for a in detectors
            )
            controls_changed = any(
                not self._values_equal(
                    self._get_alias_value(row_dict, a),
                    self._get_alias_value(prev_row, a)
                )
                for a in controls
            )
            if detectors_same and controls_changed:
                removed += 1
                continue
            pcm2 = self._get_alias_value(row_dict, "pcm2")
            if pcm2 is not None and pcm2 == 0:
                removed += 1
                continue
            filtered.append((ts_ns, row_dict))
        return filtered

    # ------------------------------------------------------------------
    # Day processing
    # ------------------------------------------------------------------

    def process_day(self, day, log_fn=print):
        start_dt = day.replace(hour=5, minute=0, second=0, microsecond=0)
        end_dt = day.replace(hour=23, minute=0, second=0, microsecond=0)
        start_ns = int(start_dt.timestamp() * 1e9)
        end_ns = int(end_dt.timestamp() * 1e9)

        samples_by_pv = {}
        for alias, pv in self.config["pvs"].items():
            raw = self.cpva_fetch_samples_chunked(pv, start_ns, end_ns, log_fn=log_fn)
            parsed = []
            for s in raw:
                ts_ns = s.get("time")
                if ts_ns is None:
                    continue
                value = self.cpva_decode_value(s)
                if not isinstance(value, (int, float)):
                    continue
                parsed.append((int(ts_ns), float(value), ""))
            samples_by_pv[pv] = parsed

        rows = self.merge_samples_sample_hold(samples_by_pv, list(self.config["pvs"].values()))
        rows = self.remove_master_only_rows(rows, self.config["master_pv"])
        rows = self.filter_master_multiple_rows(rows, self.config["master_pv"], self.config["master_multiple"])
        rows = self.remove_fake_hour_boundary_rows(rows, self.config["master_pv"])
        rows = self.apply_conditions(rows, self.config.get("conditions", []))
        rows = self.remove_ln36_only_rows(rows, self.config["pvs"].get("ln36", "ln36"))
        rows = self.remove_suspicious_rows(rows)

        log_fn(f"{day:%Y-%m-%d}: {len(rows)} rows")

        if not rows:
            log_fn(f"{day:%Y-%m-%d}: skipped (0 rows)")
            return

        saved = self.save_day(rows, day)
        if saved:
            log_fn(f"Saved {saved}")

    def save_day(self, rows, day):
        data = []
        for ts_ns, row_dict in rows:
            row = {"timestamp": ts_ns}
            for alias, pv_name in self.config["pvs"].items():
                if pv_name in row_dict:
                    value, _ = row_dict[pv_name]
                    row[alias] = value
            data.append(row)

        df = pd.DataFrame(data)
        if df.empty:
            return None

        year_dir = self.repository_dir / str(day.year)
        year_dir.mkdir(parents=True, exist_ok=True)
        target_file = year_dir / f"{day:%Y-%m-%d}.parquet"

        for col in df.columns:
            if col != "timestamp":
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df.to_parquet(target_file, index=False)
        return target_file

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------

    def run(self, log_fn=print, stop_flag=None):
        state = self.load_state()

        if state["last_processed_day"]:
            start_day = (
                datetime.strptime(state["last_processed_day"], "%Y-%m-%d")
                + timedelta(days=1)
            )
        else:
            start_day = datetime.strptime(
                self.config.get("start_date", "2026-01-01"), "%Y-%m-%d"
            )

        today = datetime.now()
        current_day = start_day

        while current_day.date() <= today.date():
            if stop_flag and stop_flag():
                log_fn("Stopped by user.")
                break

            try:
                self.process_day(current_day, log_fn)
            except Exception as e:
                log_fn(f"ERROR {current_day:%Y-%m-%d}: {e}")

            if current_day.date() < today.date():
                state["last_processed_day"] = current_day.strftime("%Y-%m-%d")
                self.save_state(state)

            current_day += timedelta(days=1)

        log_fn("Builder finished.")
