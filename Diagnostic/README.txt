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

### Notifications — adding a second Webex room and e-mail (Seznam / Outlook)

The app can already send alerts to **several Webex rooms at once** (one bot broadcasts to N rooms) — no code change needed, just fill in the settings in the GUI.

#### Adding a second Webex room

1. **PV Monitor** tab → **Settings** → **Notification channels** → tick **Webex**, then in the **Webex** section set **Mode = bot**.
2. In the **Bot token** field enter the bot's token (the same for all rooms — one bot broadcasts to all).
3. In the **Rooms** table click **Add** and fill in:
   - **Name** — any label
   - **Room ID** — the room's ID (see below how to get it)
   - **On** — tick so alerts go to it
   - **Listen for commands** — at most one room may be ticked (the bot reads two-way commands from a single room only)
4. **Save**.

#### How to get the Room ID (those "codes")

The Webex API returns only the rooms the bot is a **member** of — not every room in general. Steps:

1. **Add the bot to the target room as a regular participant**
   - Open Webex (app or web) → the room → the **People** icon → **Add People**
   - Enter the bot's e-mail (format `something@webex.bot`) and confirm
   - The bot's e-mail (and its access token, the same as `webex_bot_token`) is at https://developer.webex.com/my-apps → click the bot

2. **Call the API that lists all of the bot's rooms** — easiest right in the browser, without curl/PowerShell:
   - Go to https://developer.webex.com/docs/api/v1/rooms/list-rooms
   - In the **Try It** panel on the right paste the bot token into the Authorization field
   - Click **Run** — it returns JSON with every room the bot is a member of

   Alternative via PowerShell:
   ```powershell
   $resp = Invoke-RestMethod -Uri "https://webexapis.com/v1/rooms" -Headers @{Authorization="Bearer <bot_token>"}
   $resp.items | Select-Object title, id
   ```
   or in bash:
   ```bash
   curl -H "Authorization: Bearer <bot_token>" https://webexapis.com/v1/rooms
   ```

3. **Find the right room and copy its `id`** — the output looks like this (abbreviated):
   ```json
   {
     "items": [
       { "id": "Y2lzY29zcGFyazovL3VybjpURUFN...", "title": "Diagnostics – second room", "type": "group" },
       { "id": "Y2lzY29zcGFyazovL3VybjpURUFN...", "title": "Main", "type": "group" }
     ]
   }
   ```
   Use `title` (the room name as you see it in Webex) to find the right one and copy the value of its `id` — that is exactly what goes into the **Room ID** column in the Rooms table in Settings.

   Note: if you are only just adding the bot to the room, it shows up in the room list after it is added — not before.

#### E-mail (SMTP) — Seznam.cz and Outlook.com

Settings → Notification channels → tick **Email**, then in the **Email (SMTP)** section:

| Provider | SMTP host | Port | Security | Note |
|---|---|---|---|---|
| Seznam.cz | `smtp.seznam.cz` | 587 | starttls | A normal mailbox password works if 2FA is not enabled |
| Outlook.com / Microsoft 365 | `smtp-mail.outlook.com` (or `smtp.office365.com` for corporate M365) | 587 | starttls | With 2FA enabled an app password is required; a normal password is rejected |

**Username** = the full e-mail address, **From address** = the sending address (may be the same), **Recipients** = the table of alert recipients (tick **On** for everyone who should receive e-mails).

Where to find/create an app password:
- **Seznam.cz**: sign in at email.seznam.cz → Settings → Security → "App passwords" → create a new one, paste it into the **Password** field.
- **Outlook.com / Microsoft 365**: https://account.microsoft.com/security → Security → Advanced security options → App passwords (available only when two-factor authentication is enabled).

The password/token is encrypted automatically on **Save** (Windows DPAPI, for the current Windows account only) — see `secrets_util.py`. A readable password is never committed to git; an alternative is to write `${ENV:NAME}` and keep the password in an environment variable.

#### Microsoft Teams

The app sends alerts via the classic **Incoming Webhook** connector ("MessageCard" format) — not the newer Power Automate Workflows.

1. In Teams open the target channel → **...** (More options) → **Connectors** (or Manage channel → Connectors).
2. Find **Incoming Webhook** → **Configure** (or Add).
3. Enter a name (e.g. "PV Monitor alerts"), optionally upload an icon → **Create**.
4. Copy the generated webhook URL.
5. In the app: Settings → Notification channels → tick **Teams** → paste the copied URL into the **Incoming Webhook URL** field → **Save**.
6. Test it with the **Send test to Teams** button.

**Note:** Microsoft is gradually retiring the old "Office 365 Connectors" (classic Incoming Webhooks) in favor of Workflows. If the **Connectors** option is missing in a given team/channel, the tenant has already been migrated — then you must use the Workflows template "Post to a channel when a webhook request is received". That, however, expects a different payload format (Adaptive Card, not MessageCard), so it would require a code change in `alerting.py` (`build_messagecard`) for the messages to render correctly.

---

### Webex bot commands (two-way control)

When **Listen for commands** is enabled in Settings (with `Mode = bot` and a bot token set), the bot reads commands from one room and replies into it. Send a command by **@mentioning the bot and typing the command** — order doesn't matter (`@Diagnostics /help` and `/help @Diagnostics` both work). If you just @mention the bot without a recognized command, it replies with this help.

| Command | What it does |
|---|---|
| `/help`, `/?` | Print the command list (also works without a slash: `help`, `?`, `commands`, or just @mentioning the bot) |
| `/status` | All PVs with current value and state (ok / warning / alarm / off / no data) + whether monitoring is running |
| `/list` | List the configured PVs |
| `/plot <pv>` | Post a current plot of a PV as a PNG (window set by "Alert plot window" in Settings) |
| `/graph <pv\|all>` | Set which PV the live graph in the GUI shows (`all` = every PV) |
| `/start` | Turn monitoring on |
| `/stop [hours]` | Turn monitoring off; with a number it auto-resumes after that many hours (e.g. `/stop 10`), without a number it stays off |
| `/enable <pv>` | Enable alerting for a PV |
| `/disable <pv>` | Disable alerting for a PV |
| `/window <minutes>` | Live-graph time window |
| `/yaxis <lo> <hi>` | Fixed Y range for the live graph |
| `/yaxis auto` | Return the Y axis to autoscale |

Notes:
- PV names accept partial, case-insensitive matches (`/plot chiller` finds "DA3 Chiller"); if several PVs match, the bot replies with the options.
- If a **Command allowlist** is set in Settings, commands are accepted only from the listed e-mails; others get a polite "not allowed" reply.
- The bot reads commands only from the room that has **Listen for commands** ticked in the Rooms table (at most one).

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
