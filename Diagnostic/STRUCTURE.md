# Diagnostic — STRUCTURE

> Verified against source: 2026-08-20 · `monitor_tab.py` 6994 L · `alerting.py` 1178 L ·
> `remote_launcher.py` 487 L · `main.py` 395 L · `shared_pvs.py` 364 L ·
> `bot_commands.py` 297 L · `cpva_api.py` 263 L · `notify_provision.py` 238 L ·
> `memstats.py` 187 L · `operation_history_logic.py` 128 L · `secrets_util.py` 70 L ·
> tests: `test_alerting.py` 565 L · `test_monitor_frozen.py` 246 L ·
> `test_bot_commands.py` 243 L

PySide6 app: live PV monitoring and alerting off the CPVA archive, with a two-way
Webex bot. The **PV Monitor** tab is the program in practice.

User-facing documentation: `ReadMe_Diagnostic.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Diagnostic_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Modules

| Module | Role |
|--------|------|
| `main.py` | Entry point, window, splash, tabs, the run-status/lock files. **pandas and matplotlib are imported lazily** (`operation_history_logic` inside `HistoryTab._run`, `monitor_tab` inside `main()` after the splash) — on a cold file cache those imports cost tens of seconds, and this way the splash paints first. |
| `monitor_tab.py` | The whole PV Monitor: table, graph, threshold/condition editing, Settings, share publishing, alert dispatch, Webex commands. |
| `alerting.py` | Alert state machine + the three notification channels; the Qt-free series checks (`classify_trend`, `detect_frozen`); the run-status file. |
| `cpva_api.py` | Thin CPVA REST client, and `get_app_dir()` — every path in the program resolves through it. |
| `shared_pvs.py` | The PV list + shared settings on the scratch share. |
| `secrets_util.py` | Windows DPAPI encrypt/decrypt; `resolve_secret()` also understands `${ENV:NAME}`. |
| `notify_provision.py` | Bakes / reads `notify_provision.dat`, the channel settings shipped inside the build. |
| `bot_commands.py` | Grammar of the chat commands. Qt-free on purpose so it can be tested alone. |
| `memstats.py` | Windows memory figures via `ctypes` (no psutil): this process's **commit** and working set, and the PC's commit charge/limit. Used by the status line, the half-hourly log line and the listener's console. |
| `remote_launcher.py` | Standalone always-on Webex listener that starts the app on `/run`. Built as its own exe (`extra_exes` in `build_config.json`). |
| `operation_history_logic.py` | The History tab's drift analysis. **Dormant** — see below. |

### Dormant / leftover

- **History cannot run.** It needs `MasterOperations.parquet` next to the exe, and the
  module that wrote that file was removed on 2026-06-29. Without the file the tab
  reports it and stops.
- `DataRepository/`, `RampingRepository/`, `builder_config.json`,
  `builder_state.json` — leftovers of the removed repository pipeline. Nothing in
  this folder reads them. The CPVA fetching and the ramping repository moved to **CSS
  Logger**; the pulser image check became **Pulser Monitor**.

---

## Data flow

```
CPVA archive (REST, https://10.78.0.57:8443)
  │  cpva_api.cpva_fetch_samples()
  ▼
monitor_tab._PollWorker       every poll_interval_s, poll_max_workers at a time
       _BackfillWorker        fills the graph history at launch
       _LearnWorker           derives thresholds from N days of archive
  ▼
PVRuntime.samples ──► PVTableModel ──► GraphPanel
  ├─ alerting.AlertEvaluator     thresholds + debounce/settle + re-notify
  ├─ alerting.detect_frozen      "not updating"
  ▼
alerting.NotificationHub ──► Teams webhook / SMTP / Webex rooms
                                    ▲
                                    └── _CmdPollWorker → bot_commands
```

---

## Configuration — three homes, on purpose

| What | Where | Why there |
|------|-------|-----------|
| PV list (thresholds, conditional rules, valid range, groups) + the `settings` block | scratch share, `Diagnostic\monitor_pvs_shared.json` | one authoritative copy; every machine agrees |
| Teams / Email / Webex incl. credentials | inside the build, `notify_provision.dat` | a DPAPI blob in a local JSON is worthless to any other account or PC |
| Share location + timeout, resolved-root cache, graph PV-list / legend placement | this PC, `monitor_config.json` | the first points the PC at the share; the last is a personal view preference — sharing it would reshuffle everyone's graph |
| Is the app up / is it tracking | `%APPDATA%\Diagnostic\run_status.json` | the app has two homes (source folder and a build under `C:\Dev\dist`); a file beside the program would be a *different* file for each, so the watcher would miss a copy started the other way |
| Legacy PID file | `diagnostic.lock` | fallback for a copy started by an older build |

`monitor_config.json` also keeps a **full local mirror** of the shared list, so a PC
that cannot reach the share still starts with the last known configuration —
read-only for that session, so it cannot publish a stale copy over the group's.

`shared_settings_subset()` is the filter: everything the Settings dialog configures,
minus `SHARE_LOCAL_ONLY_KEYS` and minus `notify_provision.PROVISIONED_KEYS`. Unknown
keys are dropped, so a hand-edited or stale shared file cannot inject settings this
version does not understand. `DEFAULT_SETTINGS` is the single source of truth for keys
and defaults; `_migrate_settings` upgrades the old single-email / single-room schema.

`load_config()` applies the provisioned channels **over** the local settings;
`save_config()` strips them again (atomically, via `shared_pvs.write_json_atomic`), so
the credentials never sit in a readable JSON next to the exe.

---

## shared_pvs.py — the two constraints that shape it

| Function | Role |
|----------|------|
| `candidate_roots(override)` / `shared_file_for(root)` | the two share spellings, in a **fixed order** |
| `_first_reachable(roots, timeout_s)` | probes them **concurrently** but returns the first reachable entry **in that fixed order**, never whichever answered first — a machine that can reach both legs must always pick the same file |
| `resolve_root(override, cached_root, timeout_s)` | with the cache |
| `read_shared(path)` / `load_shared(...)` / `_load_shared_blocking(...)` | read + the bounded, threaded entry point |
| `write_json_atomic(path, obj)` | temp file + `os.replace` |
| `backup_once(path)` | `.bak.json` keeps the losing copy of a concurrent write |
| `write_shared(path, pv_dicts, settings, expect_mtime)` | publish, with a last-writer-wins mtime check |
| `SharedResult` | what was loaded, from where, and whether publishing is allowed |

1. **An unreachable UNC host takes ~48 s to fail a single `os.path.isdir()`.** Every
   entry point is bounded by a timeout and does its blocking work on a **daemon**
   thread: `concurrent.futures` workers are joined at interpreter exit and
   `QThreadPool` waits in its destructor, so a non-daemon thread stuck in that call
   would delay *shutdown* by 48 s.
2. **Writes are atomic**, because `load_config()` degrades a parse error to an empty
   PV list — a half-written shared file would silently look like "no PVs configured".

Publishing is debounced by `SHARED_WRITE_DEBOUNCE_MS` (2 s): `persist()` fires on
every checkbox click and an SMB write costs tens to hundreds of ms.

---

## alerting.py

| Symbol | Role |
|--------|------|
| `AlertLevel` (IntEnum) | ok / warn / alarm ordering |
| `Thresholds`, `AlertState`, `Notification`, `EvalConfig` | the state machine's data |
| `_raw_severity(value, thr)` | the bare verdict — **no hysteresis**, deliberately |
| `AlertEvaluator` | debounce count, settle minutes, re-notify cooldown, recovery notices, and the trend-paced reminders |
| `classify_trend(samples, now_ns, lookback_s, …)` | which way the value is heading → reminders speed up or slow down |
| `detect_frozen(samples, now_ns, frozen_after_s, …)` → `FrozenInfo` | the "not updating" verdict |
| `_same_value(a, b, rel_tol, abs_tol)` | near-exact comparison — a working sensor's noise always moves the last digit; only a stuck one repeats it |
| `describe_reason`, `fmt_value`, `fmt_duration` | message text |
| `build_messagecard`, `build_textcard` | the Teams MessageCard payloads |
| `TeamsClient`, `EmailNotifier`, `WebexNotifier`, `NotificationHub` | the channels and the fan-out |
| `run_status_path` / `read_run_status` / `write_run_status` / `clear_run_status` | the file `remote_launcher.py` reads to know whether the app is up and tracking |
| `_resolve_secret(value)` | DPAPI / `${ENV:NAME}` indirection |

`WebexNotifier` both sends (text and PNG) and listens for commands.

### "Not updating" — two independent checks

- **The value never changes.** `detect_frozen` walks back through the kept history
  while the value is still *exactly* the same and measures the run. Longer than
  `frozen_after_minutes` (120) counts, and the run must be carried by at least
  `frozen_min_points` (5) samples, so two readings hours apart are never mistaken for
  a stuck sensor.
- **The newest archived sample stops advancing.** The limit is derived from the poll
  pacing (two sample windows or three poll intervals, whichever is longer, never
  under 5 minutes), so it needs no setting of its own.

The history behind the first check is the graph's, which `_BackfillWorker` pre-fills
from the archive at launch: the visible window (`graph_window_minutes`) first so the
graph draws in ~2 s, then a follow-up pass up to `frozen_after_minutes` to arm this
check, so a freeze that started over the weekend is still visible within a poll or
two of opening the app. Older data is fetched only on request (`extend_backfill`) —
the archiver caps a request at 1 h, so a wide pre-fill costs one request per hour
per PV.

`not updating` **outranks** ok/warn/alarm in the table (dark teal), and is shown even
for PVs with **On** unchecked. A threshold alert raised on a frozen PV carries the
warning in its reason. Per-PV opt-out: `frozen_check` in **Edit PV**, for values that
genuinely hold still (switch positions, setpoints, enable flags).

---

## monitor_tab.py

### Data model

| Class | Role |
|-------|------|
| `PVConfig` | one configured PV: name, display name, thresholds, conditional rule profiles, valid range, group, `frozen_check`, on/off |
| `PVRuntime` | its live state: kept `samples`, last value, alert state, frozen info |
| `PVTableModel(QAbstractTableModel)` | the table. `_alarm_status_text` / `_alarm_status_tooltip` / `_frozen_tooltip` produce the State cell, including the `⏸`-prefixed dark-red form used while alerting is disarmed |
| `PVListChoice` / `load_shared_pv_list(settings, local_pvs)` | share vs local mirror, and whether publishing is allowed |

### Workers (all `QRunnable` on a `QThreadPool`)

| Worker | Role |
|--------|------|
| `_PollWorker` | one poll pass: up to `poll_max_workers` PVs concurrently. The archiver call is I/O-bound HTTP, so a pass costs ~`ceil(N / workers) × per-request time`; too few workers make a pass overrun the interval, which skips ticks and makes the table lag |
| `_BackfillWorker` | fills the graph history at launch (`history_minutes`) |
| `_LearnWorker` + `compute_baseline(v, warn_k, alarm_k)` | derive thresholds from `learn_days_default` days of archive; `_BatchLearnController` drives it for many PVs |
| `_ChannelsWorker` | the archiver's channel list for the PV browser |
| `_AlertWorker` | dispatch, off the UI thread |
| `_ChartWorker` + `render_chart_png` / `render_pv_png` / `_fetch_series` | the PNG a chat `/plot` returns |
| `_MeIdWorker`, `_CmdPollWorker`, `_TextReplyWorker` | the Webex bot loop |
| `_SharedWriteSignals` / `_shared_write_job` | the debounced share publish |

### Dialogs and editors

`ThresholdsEditor` / `ThresholdsPopup`, `PVBrowserDialog`, `PVEditDialog`,
`GroupsDialog`, `_LearnParamsDialog`, `EmailContactsWidget`, `WebexRoomsWidget`,
`SettingsDialog`, `_AxisRangeDialog`, `_AxisDateRangeDialog`.

`_webex_mention_hint()` is not decoration: it is written on screen in **both** places
the Webex settings can appear — the editable group box (source run) and the read-only
provisioned-channels summary (deployed build) — because in a provisioned build the
editable box is hidden, and that summary is the only place a deployed user can read
the mention rule.

`_NoWheelMixin` + `_NoWheelSpinBox` / `_NoWheelDoubleSpinBox` / `_NoWheelComboBox`:
the wheel must not change a control the pointer merely hovers over.

### GraphPanel

| Piece | Role |
|-------|------|
| `_GraphPVList(QListWidget)` | the fixed, scrollable list of drawn curves left of the plot: a `_swatch_icon` (colour, dashed for pressure) + the display name, full PV name in the tooltip. `mouseMoveEvent` / `leaveEvent` / `_pv_at` drive the hover |
| `_on_pv_hover` → `_apply_hover` → `_apply_highlight` | hovered curve at double width, the rest faded to 12 %. The repaint is deferred until the pointer settles — dragging down the list crosses every row, and one full canvas redraw per row is what made the highlight feel sticky. `_pointer_on_list()` guards it: a missed leave event or a PV that dropped out of the plot would otherwise leave **every** curve faded, i.e. an apparently empty graph |
| `_panel_sig` | last list contents; a redraw that changed nothing does not rebuild (and so does not drop) the rows, so hovering survives the 1 s refresh |
| view signature | the shape of the last full redraw (curves, units, axes, placement). `refresh_data()` compares it: unchanged means the existing lines only need their new samples pushed in, which skips rebuilding axes, legend, side list and layout on every poll |
| legend | auto / a fixed corner / outside the plot (extra right margin carved out, so it can never cover data) / hidden / a dragged position. The frame is always opaque. **The list and the legend are alternatives**: picking a placement switches the list off, switching the list on suppresses the legend. `graph_pv_panel`, `graph_legend_loc`, `graph_legend_anchor` — local-only |
| `_LightNavToolbar(NavToolbar)` | the matplotlib toolbar |

The side list exists because matplotlib re-picks a "best" legend corner on every
redraw — the legend visibly jumped from corner to corner while data came in.

### Reading vs monitoring

Polling is independent of **Start monitoring**. PVs are read from launch, so the
table and the graph are always live; the button arms only threshold evaluation, alert
dispatch and the data watchdog. `toggle_monitoring` also updates `run_status.json`, so
`remote_launcher.py` can answer "is it tracking".

### Staying up for weeks

This app and `remote_launcher.py` are meant to be left running, so anything that
grows per poll is a leak with a deadline. What the OS runs out of first is not RAM
but the **commit limit** (RAM + page file, promised across all processes): at 100 %
Windows can no longer start anything, with free RAM still showing in Task Manager.

- **Shown**: `_update_status` appends `memstats.short_line()` — this process's commit
  size, its working set, and the PC's commit charge/limit — with a `⚠` at
  `MEM_WARN_PCT`. The tooltip explains both figures; `/status` carries the same line.
- **Recorded**: `_log_memory` writes the fuller wording plus the growth since launch
  every `MEM_LOG_INTERVAL_MS` (30 min). That series is the evidence — memory that
  settles is normal, memory that climbs is a leak. It is also a heartbeat: a line
  every half hour through a quiet spell.
- **Bounded**: `LogWidget.MAX_LINES` (20 000) caps the Log tab, which otherwise grew
  for as long as the window stayed open. `PVRuntime.history` was already a bounded
  `deque` (`_history_maxlen`).
- **Fixed leak**: every worker dispatch makes a `_*Signals` object parented to the
  widget, and a parented `QObject` is only destroyed with its parent — so all of them
  stayed alive. Measured at **~3 KB each**, i.e. ~0.5 GB of commit per week at a 10 s
  poll plus the 5 s Webex poll. Each dispatch site now does
  `sig.done.connect(sig.deleteLater)` **before** connecting the result handler:
  `deleteLater` is queued, so the handler still receives its payload and the object
  goes away afterwards. New worker dispatches must keep doing this.

---

## bot_commands.py — the grammar

Two structural separators, and only two, because a PV name may contain spaces:
`,` between **items** (PV names), `;` before **options**.

| Function | Role |
|----------|------|
| `parse_command(text)` → `ParsedCommand` | verb, items, options. Raises `CommandError` with a human message |
| `parse_time_spec(spec, now_ns, tz)` → `TimeRange` | `12h` / `90m` / `2d` / bare hours, `7-18`, `22-6` (crosses midnight), `today`, `yesterday`, `yesterday 7-18`, `15.8. 7-18`, `2026-08-15`. A window running past now is cut off and the reply says `(so far)`; a bare `15.8.` means the most recent 15 August |
| `parse_yaxis_spec(spec)` | `y 15-35` / `y auto` |
| `parse_plot_options(options, now_ns, …)` → `PlotOptions` | the `/plot` option list |
| `mention_help(bot_name)` | the tag reminder at the top of `/help` |

PV names are matched loosely — any unique part of the display name or the PV name,
case-insensitive; an ambiguous or unknown name is reported and nothing is sent.

The mention Webex writes into the text is stripped in `monitor_tab._on_commands`
before parsing.

---

## remote_launcher.py — the always-on listener

Qt-free, imports only `alerting.WebexNotifier`, so it stays a lightweight background
process. Built `--onefile` as its own exe (see the Dev Tools STRUCTURE for why).

| Function | Role |
|----------|------|
| `_is_command(raw)` | strips the mention, then only the **first word** has to be the command. (Fixed 2026-08-19: it used to compare the whole message with `/rundiagnostic`, so the tag Webex writes made it never match — the command looked ignored in exactly the kind of room where the tag is compulsory.) |
| `is_app_running()` / `is_tracking()` / `_pid_alive(pid)` | from `run_status.json`, with `diagnostic.lock` as the legacy fallback |
| `_version_key(name)` / `newest_build()` | version folders under `C:\Dev\dist\Diagnostic` ordered **as numbers**, so `v1.0.10` beats `v1.0.9`; a folder with no exe is skipped rather than shadowing the working version below it |
| `_source_script()` / `_python_runner()` | the fallback when there is no build: the app's own `.py`, run with a **real** `pythonw` — frozen, `sys.executable` is the listener itself and starting the app with it would only relaunch the listener |
| `launch_diagnostic()` | starts it, with `DIAGNOSTIC_START_MONITORING=1` for that process only, so the **Start monitoring on launch** setting is left alone |
| `_wait_and_report(webex, proc, what)` | answers "starting…" at once, then watches on a background thread for the app to report that it is actually tracking (240 s limit). The poll loop keeps answering meanwhile — a bot that goes deaf during a cold start looks exactly like a bot that has died |
| `load_webex_settings()` / `build_webex(settings)` | reuses the bot token, the listening room and the allowlist from `monitor_config.json` / `notify_provision.dat` — one bot identity to manage |
| `install_startup()` / `uninstall_startup()` / `_startup_shortcut()` | `--install-startup` drops a per-user Startup shortcut; nothing is installed unless asked |

On the first successful poll it only takes a baseline of the room, so an old backlog
is never answered; a failed poll does not reset that baseline.

`/run` is answered by **both** processes on purpose. The listener starts the app
when it is closed; the app's own `_handle_command` also has a `/run` branch that
replies "already running (and tracking / not tracking)". Without that branch the
app fell through to `Unknown command /run`, which was the ONLY reply /run got
whenever the listener was not running — an app that was up and tracking looked
broken. With both up the room simply sees the same answer twice.

Its console prints one `memstats` line at start and one every `MEM_LOG_INTERVAL_S`
(1 h): its own committed memory with the growth since start, plus the PC's commit
charge. This is the process that really accumulates weeks of uptime, and the console
window is the only place it can say so.

Its **own two user documents** live here, named after the deployed program and not
after this folder: `ReadMe_Diagnostic Webex listener.txt` and
`ReadMe_Diagnostic Webex listener_Full.txt`. The Launcher builds its ReadMe/Details
targets from the program folder name, which for the listener is
`Diagnostic Webex listener`, so `ReadMe_Diagnostic.txt` would never be found for it;
`b_t.py::_build_one_helper` copies both into `dist\Diagnostic Webex listener\vX.Y.Z\`.

Fixed 2026-08-20: the startup banner printed an undefined `COMMAND` (the constant is
`COMMANDS`), so `main()` raised `NameError` immediately after the configuration check
— the window opened and closed again and no command was ever answered.

---

## notify_provision.py — channels baked into the build

A freshly built exe used to open with empty Teams/Email/Webex settings, because the
credentials in `monitor_config.json` are DPAPI blobs tied to one Windows account.

| Function | Role |
|----------|------|
| `bake(settings, out_path)` | write the blob from a source run's config |
| `load()` / `_decode(text)` / `blob_path()` / `_search_dirs()` | read it next to the exe |
| `is_active()` / `describe(prov)` | the read-only summary shown in Settings |
| `_keystream(salt, n)` / `_xor(data, salt)` | the obfuscation |

**Obfuscated, not encrypted** — the program has to read it unattended, so the key is
in the code. It keeps credentials out of the UI and out of plain config files; treat
them as shared within the group and rotate them if a build leaves the group.

CLI: `python notify_provision.py bake` / `show`.

`build_config.json` lists the blob under `extra_files` and forces `win32crypt` as a
hidden import. **Check `win32crypt` is in every build**: `secrets_util.py` imports it
lazily, so PyInstaller does not see it on its own, and without it a stored secret
silently decrypts to an empty string and alerting stops working with no error.

---

## Tests (offline, no network)

```
python test_alerting.py         the alerting layer incl. detect_frozen
python test_monitor_frozen.py   the "not updating" check as the tab uses it:
                                verdict, one-shot notification, table rendering
python test_bot_commands.py     the chat command grammar
```

---

## Dependencies

```
PySide6      GUI
pandas       History tab only (lazy import)
numpy        threshold learning, series maths
matplotlib   the graph and the chat PNGs (lazy import)
requests     CPVA + the three notification channels
urllib3      HTTP (SSL verification off for the internal API)
pyarrow      parquet, via pandas — History tab only
pywin32      win32crypt / DPAPI (lazy; must be forced into the bundle)
```
