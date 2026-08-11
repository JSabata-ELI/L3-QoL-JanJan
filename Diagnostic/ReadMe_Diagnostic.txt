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
| **PV Monitor** | Live PV table + graph, thresholds and alerting to Teams / Email / Webex |
| **Log** | Run log of every tab |

---

## Files

| File | What it is |
|------|-----------|
| `main.py` | Entry point and window. Builds the tabs, shows a splash, and keeps `diagnostic.lock` (its own PID) so `remote_launcher.py` can tell whether the app is already running. pandas and matplotlib are imported **lazily** (`operation_history_logic` inside `HistoryTab._run`, `monitor_tab` inside `main()` after the splash) — on a cold file cache those imports cost tens of seconds, and this way the splash paints first. |
| `builder_logic.py` | Repository builder (see below) |
| `rampdiag_logic.py` | Ramping Diagnostix (see below) |
| `detect_suspicious_logic.py` | Anomaly detection (see below) |
| `operation_history_logic.py` | Long-term drift per waveplate (see below) |
| `monitor_tab.py` | The whole PV Monitor tab: PV table, live graph, threshold/condition editing, Settings, share publishing, alert dispatch |
| `alerting.py` | The notification channels themselves — Teams (`build_messagecard`), SMTP email, and `WebexNotifier` (send + two-way command listening) |
| `cpva_api.py` | Thin CPVA REST client + `get_app_dir()` used to resolve every path |
| `shared_pvs.py` | PV list + shared settings on the scratch share (see below) |
| `secrets_util.py` | Windows DPAPI encrypt/decrypt for stored credentials; `resolve_secret()` also understands `${ENV:NAME}` |
| `notify_provision.py` | Bakes / reads `notify_provision.dat`, the channel settings shipped inside the build |
| `remote_launcher.py` | Standalone Webex listener that starts the app on `/rundiagnostic` (see below) |
| `test_alerting.py` | Offline tests for the alerting layer |
| `build_config.json`, `icon.ico` | Build settings for Dev Tools (`extra_files` carries `notify_provision.dat`, `win32crypt` is forced into the bundle) and the app icon |

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

#### `monitor_tab.py` — PV Monitor
Live monitoring and alerting, independent of the repository pipeline.

- **Table** — one row per PV: On, name, value, updated, state, thresholds,
  conditional rules, valid range, group. Alert state is one of ok / warn / alarm /
  no-data / bad-data (out-of-range sensor reading, painted distinctly from no-data).
- **Graph** — the recent history of the selected PVs (`graph_window_minutes`).
- **Polling** — every `poll_interval_s` through `_PollWorker`, up to
  `poll_max_workers` PVs concurrently. The archiver call is I/O-bound HTTP, so a
  pass costs about `ceil(N / workers) × per-request time`; too few workers make a
  pass overrun the interval, which skips ticks and makes the table lag.
  `_BackfillWorker` fills the graph history, `_LearnWorker` + `compute_baseline`
  derive thresholds from `learn_days_default` days of archive.
- **Alerting** — raw thresholds plus `debounce_count` / `settle_minutes` (no
  hysteresis), `renotify_cooldown_minutes` reminders that speed up or slow down
  with the recent trend (`trend_*` keys), recovery notices, and a data watchdog
  that alerts once when *every* PV stops returning data for
  `data_watchdog_fail_polls` polls and once when the flow resumes.
- **Settings** — everything above, plus the notification channels (or, in a
  provisioned build, a read-only summary of them) and the share location.
  `DEFAULT_SETTINGS` is the single source of truth for keys and defaults;
  `_migrate_settings` upgrades older files.

#### `shared_pvs.py` — shared PV list on the scratch share
Every copy of the app resolves its config relative to its own folder, so the copy
on the share used to start with an empty PV list. This module keeps the list (with
each PV's thresholds, profiles and valid range) plus the shared `settings` block on
the share instead.

Two constraints shape it:

- An unreachable UNC host takes **~48 s** to fail a single `os.path.isdir()`. Every
  entry point is therefore bounded by a timeout and does its blocking work on a
  **daemon** thread — `concurrent.futures` workers are joined at interpreter exit
  and `QThreadPool` waits in its destructor, so a non-daemon thread stuck in that
  call would delay *shutdown* by 48 s.
- Writes are atomic (temp file + `os.replace`): `load_config()` swallows a parse
  error and falls back to an empty PV list, so a half-written shared file would
  silently look like "no PVs configured".

The two share names are probed concurrently but the winner is the **first
reachable entry in a fixed order**, never whichever answered first — a machine
that can reach both legs must always pick the same file. Publishing is debounced
by `SHARED_WRITE_DEBOUNCE_MS` (2 s) because `persist()` fires on every checkbox
click and an SMB write costs tens to hundreds of ms.

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

**`monitor_config.json`** (per PC)
```
settings   every key of DEFAULT_SETTINGS; the shared subset is a mirror of the
           share, the four shared_pv_list_* / _shared_pv_root_cache keys are
           local-only, and the channel keys are stripped on save (they come
           from notify_provision.dat)
pvs        local mirror of the PV list, used when the share is unreachable
```

**`monitor_pvs_shared.json`** (on the scratch share) — the authoritative PV list +
`settings` block; `.bak.json` next to it keeps the losing copy of a concurrent write.

**`diagnostic.lock`** — the running app's PID, read by `remote_launcher.py`.

---

### Notifications — adding a second Webex room, email (Seznam / Outlook), Teams

The app can already send alerts to **several Webex rooms at once** (one bot
broadcasting to N rooms) — no code change is needed, only settings in the GUI.

#### Adding a second Webex room

1. **PV Monitor** tab → **Settings** → **Notification channels** → tick **Webex**,
   then in the **Webex** section set **Mode = bot**.
2. Fill in **Bot token** (the same one for every room — one bot serves all of them).
3. In the **Rooms** table click **Add** and fill in:
   - **Name** — any label
   - **Room ID** — the room's id (how to get it, below)
   - **On** — tick it to send alerts there
   - **Listen for commands** — at most ONE room may have this ticked (the bot reads
     two-way commands from a single room)
4. **Save**.

#### How to get a Room ID

The Webex API only returns rooms the bot is a **member** of, not every room that
exists:

1. **Add the bot to the target room as an ordinary participant**
   - Webex (app or web) → the room → **People** → **Add People**
   - Enter the bot's email (`something@webex.bot`) and confirm
   - The bot's email and its access token (the same value as `webex_bot_token`) are
     at https://developer.webex.com/my-apps → click the bot

2. **List the bot's rooms** — easiest straight from the browser:
   - Open https://developer.webex.com/docs/api/v1/rooms/list-rooms
   - In the **Try It** panel on the right put the bot token in the Authorization field
   - Click **Run** — the JSON lists every room the bot belongs to

   PowerShell alternative:
   ```powershell
   $resp = Invoke-RestMethod -Uri "https://webexapis.com/v1/rooms" -Headers @{Authorization="Bearer <bot_token>"}
   $resp.items | Select-Object title, id
   ```
   or bash:
   ```bash
   curl -H "Authorization: Bearer <bot_token>" https://webexapis.com/v1/rooms
   ```

3. **Find the room and copy its `id`** — the output looks like this (shortened):
   ```json
   {
     "items": [
       { "id": "Y2lzY29zcGFyazovL3VybjpURUFN...", "title": "Diagnostics – second room", "type": "group" },
       { "id": "Y2lzY29zcGFyazovL3VybjpURUFN...", "title": "Main", "type": "group" }
     ]
   }
   ```
   Match on `title` (the room name as you see it in Webex) and copy that entry's
   `id` — that is exactly what goes into the **Room ID** column in Settings.

   Note: a room only shows up here *after* the bot has been added to it.

#### Email (SMTP) — Seznam.cz and Outlook.com

Settings → Notification channels → tick **Email**, then in **Email (SMTP)**:

| Provider | SMTP host | Port | Security | Note |
|---|---|---|---|---|
| Seznam.cz | `smtp.seznam.cz` | 587 | starttls | The normal mailbox password works as long as 2FA is off |
| Outlook.com / Microsoft 365 | `smtp-mail.outlook.com` (or `smtp.office365.com` for corporate M365) | 587 | starttls | With 2FA on you need an app password; the normal one is rejected |

**Username** = the full email address, **From address** = the sending address (may
be the same), **Recipients** = the table of alert recipients (tick **On** for
everyone who should get mail).

Where to create an app password:
- **Seznam.cz**: email.seznam.cz → Settings → Security → "App passwords" → create a
  new one and paste it into **Password**.
- **Outlook.com / Microsoft 365**: https://account.microsoft.com/security →
  Security → Advanced security options → App passwords (only available with
  two-factor authentication enabled).

On **Save** the password/token is encrypted automatically (Windows DPAPI, valid for
the current Windows account only) — see `secrets_util.py`. A readable password is
never written to git; the alternative is to store `${ENV:NAME}` and keep the secret
in an environment variable.

#### Microsoft Teams

Alerts go through the classic **Incoming Webhook** connector (the "MessageCard"
format), not through the newer Power Automate Workflows.

1. In Teams open the target channel → **...** (More options) → **Connectors** (or
   Manage channel → Connectors).
2. Find **Incoming Webhook** → **Configure** (or Add).
3. Give it a name (e.g. "PV Monitor alerts"), optionally upload an icon → **Create**.
4. Copy the generated webhook URL.
5. In the app: Settings → Notification channels → tick **Teams** → paste the URL
   into **Incoming Webhook URL** → **Save**.
6. Verify with **Send test to Teams**.

**Note:** Microsoft is retiring the old "Office 365 Connectors" (classic Incoming
Webhooks) in favour of Workflows. If a team/channel has no **Connectors** entry,
that tenant has already been migrated and you have to use the Workflows template
"Post to a channel when a webhook request is received". That one expects a
different payload (Adaptive Card, not MessageCard), so it would need a change in
`alerting.py` (`build_messagecard`) for the messages to look right.

#### Remote start over Webex (`remote_launcher.py`)

`main.py`'s own Webex listener only runs while the app is open, so a separate,
always-on watcher can start it on request:

- Run it with `pythonw.exe remote_launcher.py` — it is deliberately Qt-free and
  imports only `alerting.WebexNotifier`, so it stays a lightweight background process.
- It reuses the bot token, the room marked **Listen for commands** and the command
  allowlist from `monitor_config.json` / `notify_provision.dat`, so there is only
  one bot identity to manage.
- Command: **`/rundiagnostic`** in that room. The sender must be in
  `webex_command_allowlist` (when the allowlist is non-empty), otherwise the bot
  replies that they are not allowed.
- It checks `diagnostic.lock` (the PID `main.py` writes) so a second copy is never
  spawned; a stale lock from a crashed run is cleaned up.
- On the first successful poll it only takes a baseline of the room, so an old
  backlog is never answered; a failed poll does not reset that baseline.
- For autostart: put a shortcut to `pythonw.exe "<path>\remote_launcher.py"` in
  `shell:startup`, or add an "At log on" Task Scheduler trigger. The script does
  not install itself.

---

### Reading vs. monitoring

Polling is independent of the **Start monitoring** button. PVs are read at the
configured poll interval from launch, so the table, the Value/Updated columns and
the graph always show live data. The button only arms the alerting side:
threshold evaluation, alert dispatch and the data watchdog.

While alerting is disarmed, the **State** cell keeps the real severity of the
reading but is painted dark red and prefixed with `⏸` (e.g. `⏸ ok`), and the
status bar reads `stopped (reading only)` — the value is true, nobody is watching
it. A row whose **On** box is unchecked still shows `off` as before.

### What is shared and what is per-PC

Nothing has to be configured twice. Where each piece of configuration lives:

| Configuration | Lives in | Notes |
|---|---|---|
| PV list, thresholds, conditional rules, valid ranges, groups | scratch share, `Diagnostic\monitor_pvs_shared.json` | published on every change (2 s debounce), last writer wins, `.bak.json` keeps the loser |
| Poll pacing, debounce/settle, re-notify + trend behaviour, learn defaults, valid-range defaults, watchdog, graph and alert-plot windows | same shared file, `settings` block | 26 keys; see `shared_settings_subset()` |
| Teams / Email / Webex incl. credentials | the build itself, `notify_provision.dat` | see below |
| Share location + timeout, resolved-root cache | this PC only, `monitor_config.json` | this is what points the PC at the share |

The shared file also keeps a full local mirror in `monitor_config.json`, so a PC
that cannot reach the share still starts with the last known list and settings
(read-only for the session — it will not publish, so it cannot overwrite the
group's config with a stale copy).

A shared file written by an older version has no `settings` block; the first
upgraded copy publishes its own settings once as the baseline and says so in the
Log tab.

### Notification channels baked into the build

A freshly built .exe used to open with empty Teams/Email/Webex settings: the
credentials in `monitor_config.json` are DPAPI blobs tied to one Windows account,
so they are worthless to any other user or PC.

The channel settings therefore ship inside the build, in `notify_provision.dat`
(see `notify_provision.py`):

- `load_config()` applies the blob **over** the local config, so every copy
  alerts through the same accounts with no per-PC setup.
- `save_config()` strips those keys again, so the credentials never sit in a
  readable JSON next to the exe.
- Settings shows a read-only summary instead of the three channel sections; the
  **Send test** buttons still work, so the field can verify connectivity.
- `build_config.json` lists the blob in `extra_files`, so the builder copies it
  next to the exe (and forces `win32crypt` into the bundle). The Copy Manager
  then takes it along automatically: it deploys the exe, every `*.py`, and every
  other loose file in the version folder — subfolders are the one thing it does
  not copy.

To change a channel or rotate a credential: edit it in a source run (`python
main.py`), then re-bake and rebuild:

```
python notify_provision.py bake     # reads monitor_config.json, writes the blob
python notify_provision.py show     # what the blob currently carries
```

The blob is obfuscated, not encrypted — the program has to read it unattended, so
the key is in the code. It keeps credentials out of the UI and out of plain
config files; treat them as shared within the group and rotate them if a build
leaves the group.

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
pywin32      win32crypt — DPAPI encryption of stored credentials
```

`win32crypt` is imported lazily by `secrets_util.py`, so PyInstaller does not see
it on its own. It is forced into the bundle via `build_config.json` — check that it
is present in every build, because without it a stored secret silently decrypts to
an empty string and alerting stops working with no error.
