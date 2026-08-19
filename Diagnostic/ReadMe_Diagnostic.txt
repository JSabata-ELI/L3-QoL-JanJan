# Diagnostic

PySide6 GUI for live monitoring and alerting on L3 beamline PVs. It reads values
from the CPVA archive, shows them in a table and a graph, compares them against
limits and sends alerts to Teams / Email / Webex. A second tab does long-term
drift analysis on stored waveplate operations.

---

## Quick Overview

| Tab | What it does |
|-----|--------------|
| **PV Monitor** | Live PV table + graph, thresholds, "not updating" check, alerting to Teams / Email / Webex, and a two-way Webex bot |
| **History** | Detects >3 % deviation from a 30-day baseline per waveplate operation and saves a PNG per flagged waveplate |
| **Log** | The PV Monitor's run log (the same widget, shown as its own tab) |

The PV Monitor is the whole program in practice. The repository-building tabs
(Builder / Browser / Suspicious / Diagnostix / Filter Plot) that earlier versions
had were removed on 2026-06-29 together with `builder_logic.py`,
`rampdiag_logic.py`, `detect_suspicious_logic.py` and `pulser_monitor_tab.py`.
What replaced them:

- CPVA fetching and the ramping repository → the **CSS Logger** program
- the pulser image check → the **Pulser Monitor** program

Two consequences worth knowing:

- `DataRepository\`, `RampingRepository\`, `builder_config.json` and
  `builder_state.json` are leftovers from that era. Nothing in this folder reads
  them any more.
- **History cannot run as things stand.** It needs `MasterOperations.parquet`
  next to the exe, and the module that used to write that file is gone. Without
  the file the tab reports `MasterOperations.parquet not found` and stops. Either
  bring a copy of the file along, or treat the tab as dormant.

---

## Files

| File | What it is |
|------|-----------|
| `main.py` | Entry point and window. Builds the tabs, shows a splash, and keeps `diagnostic.lock` (its own PID) so `remote_launcher.py` can tell whether the app is already running. pandas and matplotlib are imported **lazily** (`operation_history_logic` inside `HistoryTab._run`, `monitor_tab` inside `main()` after the splash) — on a cold file cache those imports cost tens of seconds, and this way the splash paints first. |
| `monitor_tab.py` | The whole PV Monitor tab: PV table, live graph, threshold/condition editing, Settings, share publishing, alert dispatch, Webex commands. By far the largest module (~6.4 k lines) |
| `alerting.py` | The alert state machine and the notification channels — Teams (`build_messagecard`), SMTP email, and `WebexNotifier` (send + two-way command listening). Also the Qt-free sample-series checks: `classify_trend` (reminder pacing) and `detect_frozen` ("not updating") |
| `cpva_api.py` | Thin CPVA REST client + `get_app_dir()` used to resolve every path |
| `shared_pvs.py` | PV list + shared settings on the scratch share (see below) |
| `secrets_util.py` | Windows DPAPI encrypt/decrypt for stored credentials; `resolve_secret()` also understands `${ENV:NAME}` |
| `notify_provision.py` | Bakes / reads `notify_provision.dat`, the channel settings shipped inside the build |
| `bot_commands.py` | Grammar of the chat commands — `,` between items, `;` before options, and the time/Y-range parsers. Qt-free on purpose so it can be tested alone |
| `remote_launcher.py` | Standalone Webex listener that starts the app on `/rundiagnostic` (see below) |
| `operation_history_logic.py` | Long-term drift per waveplate, behind the History tab (see the note above about its missing input file) |
| `test_alerting.py` | Offline tests for the alerting layer, including the frozen-value detector (`python test_alerting.py`) |
| `test_monitor_frozen.py` | Offline tests for the "not updating" check as the PV Monitor uses it: verdict, one-shot notification, table rendering (`python test_monitor_frozen.py`) |
| `test_bot_commands.py` | Offline tests for the chat command grammar (`python test_bot_commands.py`) |
| `build_config.json`, `icon.ico` | Build settings for Dev Tools (`extra_files` carries `notify_provision.dat`, `win32crypt` is forced into the bundle) and the app icon |
| `STRUCTURE.md` | Developer map of every module, class and function |

Leftovers from the removed repository pipeline, kept only so old data is not
thrown away: `DataRepository\`, `RampingRepository\`, `builder_config.json`,
`builder_state.json`.

---

## Detailed Description

### Data flow

```
CPVA archive (REST, https://10.78.0.57:8443)
  │
  │  cpva_api.cpva_fetch_samples()
  ▼
monitor_tab._PollWorker          every poll_interval_s, poll_max_workers at a time
  │      _BackfillWorker         fills the graph history at launch
  │      _LearnWorker            derives thresholds from N days of archive
  ▼
PVRuntime.samples (in memory)  ──►  PVTableModel (table)  ──►  GraphPanel (plot)
  │
  ├─ alerting.AlertEvaluator     thresholds + debounce/settle + re-notify
  ├─ alerting.detect_frozen      "not updating"
  ▼
alerting.NotificationHub  ──►  Teams webhook / SMTP email / Webex rooms
                                        ▲
                                        └── Webex chat commands come back in
                                            (_CmdPollWorker → bot_commands)

configuration
  monitor_pvs_shared.json (scratch share)  ← the authoritative PV list + settings
  monitor_config.json     (this PC)        ← share location, local mirror, legend
  notify_provision.dat    (in the build)   ← the notification channels
```

The History tab is a separate, offline path:

```
MasterOperations.parquet ──► operation_history_logic.py ──► HistoryPlots/{waveplate}_{metric}.png
```

---

### Module descriptions

#### `operation_history_logic.py` — Operation History
Reads `MasterOperations.parquet` and analyses long-term drift per waveplate position.

- Metrics tracked: `sbw4_green`, `green_ptm1`, `ptm1_pap1`, `pcm2_green`, `sbw4_ptm1`
- Baseline: 30-day rolling median.
- Deviation threshold: > 3 % flags the point.
- Output: PNG line plots saved to `HistoryPlots/{waveplate}_{metric}.png` for the 25 most common waveplates.

Nothing in this folder produces its input file any more — see the note under
Quick Overview.

---

#### `monitor_tab.py` — PV Monitor
Live monitoring and alerting — in practice the whole program.

- **Table** — one row per PV: On, name, value, updated, state, thresholds,
  conditional rules, valid range, group. Alert state is one of ok / warn / alarm /
  no-data / bad-data (out-of-range sensor reading, painted distinctly from no-data)
  / not-updating (see below).
- **Graph** — the recent history of the selected PVs (`graph_window_minutes`).
  Right-click a tick-label area for that axis's range/autoscale, or the plot
  itself for the view options: grid lines and **Legend** — auto (matplotlib
  picks the emptiest corner), a fixed corner, outside the plot (extra right
  margin is carved out, so it can never cover data), or hidden. The legend can
  also be dragged with the left button; that position is remembered as
  "Dragged position". Its frame is always opaque, so wherever it lands it hides
  a trace cleanly instead of blending with it. Placement is stored locally
  (`graph_legend_loc` / `graph_legend_anchor`) and never published to the share.
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
- **"Not updating" check** — a per-PV freshness check for data that arrives but
  is no longer live (`frozen_*` keys, `alerting.detect_frozen`). See below.
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

**`builder_config.json`**, **`builder_state.json`** — leftovers from the removed
repository builder. Nothing reads them; they are safe to delete once the old
`DataRepository\` data is no longer wanted.

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

#### Talking to the bot (chat commands)

In the room marked **Listen for commands** the bot answers one-line commands.
`/help` prints the whole thing in the chat; the grammar itself lives in
`bot_commands.py` and is tested by `test_bot_commands.py`.

Two separators, and only these two are structural (a PV name may contain spaces):

- `,` separates **items**, usually PV names:
  `/plot Chiller 1, Chiller 2, Chiller 3`
- `;` separates **options** that configure the command:
  `/plot Chiller 1, Chiller 2, Chiller 3; 7-18; y 15-35`

PV names are matched loosely — any unique part of the display name or the PV name,
case-insensitive. An ambiguous or unknown name is reported and nothing is sent.

Time windows (Europe/Prague, may be given in any option position):

| Written | Means |
|---|---|
| `12h`, `90m`, `2d`, `12` | the last N hours / minutes / days, ending now (bare number = hours) |
| `7-18`, `7:30-18:00` | that clock window today; if it has not started yet, yesterday's |
| `22-6` | crosses midnight |
| `today`, `yesterday` | that whole day (`today` ends now) |
| `yesterday 7-18` | clock window on that day |
| `15.8. 7-18`, `15.8.2026 7-18`, `2026-08-15 7-18` | clock window on that date |
| `2026-08-15`, `15.8.` | that whole day |

A window running past the current time is cut off at now and the reply says
`(so far)`. A bare `15.8.` with no year means the most recent 15 August.

`y 15-35` fixes the Y range of the plot, `y auto` leaves it automatic.

The commands themselves:

| Command | What it does |
|---|---|
| `/help`, `/?` | this cheat sheet (a bare `help` with no slash works too) |
| `/status [pv, pv]` | value + state for all PVs, or only the named ones |
| `/alarms` | only PVs currently in warning/alarm |
| `/list` | the configured PVs |
| `/plot <pv, pv, …>[; window][; y lo-hi]` | **one** graph with a curve per PV. `/plot all` takes every PV. A single PV also gets its warning/alarm lines — an overlay does not, since the lines would belong to no visible curve. Without a window option the **Alert plot window** from Settings is used |
| `/start` | alerting on (PVs are read and plotted either way) |
| `/stop [hours]` | alerting off; with hours, auto-resume later |
| `/enable <pv, pv>` / `/disable <pv, pv>` | alerting per PV |
| `/datawatchdog on\|off` | the "no data at all" alert; no argument shows the state |
| `/graph <pv\|all>` | what the app window itself shows (one PV or all) |
| `/window <minutes>` | time window of that live graph |
| `/yaxis <lo-hi>`, `/yaxis auto` | Y range of that live graph |

The rendered plot goes to **every** enabled channel, not only to the chat: Webex
rooms get the PNG, e-mail gets it as an attachment, Teams gets the text (an
Incoming Webhook cannot carry an image). If no PV had archived data in the
window, the message still goes out and says so.

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

### "Not updating" — a PV that answers but is not live

The data watchdog only fires when *every* PV goes silent. A single PV can fail in
a much quieter way: the archiver keeps answering, with fresh timestamps and a
plausible number, but the number never moves — a dead sensor, a stuck IOC, a
control system republishing its last value. Nothing else in the monitor notices:
the value sits inside its limits and looks like a beautifully steady reading, or
sits outside them and alarms every 30 minutes on data that is days old.

Each poll therefore also asks "is this reading still alive?", in two ways:

- **The value never changes.** `alerting.detect_frozen()` takes the newest
  reading, walks back through the PV's kept history while the value is still
  *exactly* the same, and measures how long that run has lasted. The comparison
  is near-exact on purpose: a working sensor's noise always moves the last digit,
  only a stuck one repeats it. Longer than **`frozen_after_minutes`** (120 by
  default) counts as not updating. An unchanged run must also be carried by at
  least `frozen_min_points` (5) samples, so two readings hours apart are never
  mistaken for a stuck sensor.
- **The newest archived sample stops advancing.** The archiver answers, but with
  data from minutes or hours ago. The limit is derived from the poll pacing (two
  sample windows or three poll intervals, whichever is longer, never under
  5 minutes), so it needs no setting of its own.

The history behind the first check is the same one the graph uses, which
`_BackfillWorker` pre-fills from the archive at launch (`history_minutes`, 12 h by
default). A freeze that started over the weekend is therefore visible within a
poll or two of opening the app — it does not take 2 hours of runtime to notice.

What it looks like:

- **State** cell reads `not updating` on its own dark-teal background — it
  outranks ok/warn/alarm, because a limit verdict on a dead reading means
  nothing. It is shown for PVs with **On** unchecked too, since a stuck sensor is
  worth seeing whether or not that PV may raise alerts.
- **Value** and **Updated** turn italic: still the last known reading, no longer
  a live one. Their tooltips say since when, and whether the freeze may be older
  than the data kept here.
- The status bar gains `⚠ N not updating`.
- `/status` and `/alarms` list it with the reason; a frozen PV is reported in
  `/alarms` as the data fault it is, not as whatever its dead reading scores
  against the limits.
- With **`frozen_alert_enabled`** on, one notification goes out when a PV stops
  updating and one when it starts changing again — never repeated, because this
  is a data fault, not a value excursion. Losing the data entirely does *not*
  count as recovery (that is the ordinary no-data state).
- A threshold alert raised on a frozen PV carries `⚠ but this PV is NOT UPDATING
  (…)` in its reason, so a Warning/Alarm message can no longer read as a fresh
  measurement.

Values that genuinely hold still for hours — switch positions, setpoints, enable
flags — would be flagged for ever, so **Edit PV** has *"Report this PV as not
updating when its value never changes"* to opt out per PV (`frozen_check`), and
Settings has the global switch, the time limit and the alert switch.

### What is shared and what is per-PC

Nothing has to be configured twice. Where each piece of configuration lives:

| Configuration | Lives in | Notes |
|---|---|---|
| PV list, thresholds, conditional rules, valid ranges, groups | scratch share, `Diagnostic\monitor_pvs_shared.json` | published on every change (2 s debounce), last writer wins, `.bak.json` keeps the loser |
| Poll pacing, debounce/settle, re-notify + trend behaviour, learn defaults, valid-range defaults, watchdog, "not updating" check, graph and alert-plot windows | same shared file, `settings` block | 30 keys; see `shared_settings_subset()` |
| Teams / Email / Webex incl. credentials | the build itself, `notify_provision.dat` | see below |
| Share location + timeout, resolved-root cache | this PC only, `monitor_config.json` | this is what points the PC at the share |
| Graph legend placement | this PC only, `monitor_config.json` | a personal view preference; sharing it would reshuffle everyone's graph |

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
