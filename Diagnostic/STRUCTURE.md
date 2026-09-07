# Diagnostic — STRUCTURE

> Verified against source: 2026-09-02 · `monitor_tab.py` 8679 L · `okbase_menu.py` 1782 L ·
> `alerting.py` 1178 L · `chart_history.py` 878 L · `remote_launcher.py` 711 L ·
> `cpva_api.py` 585 L · `bot_commands.py` 481 L · `edge_cdp.py` 471 L ·
> `main.py` 395 L · `shared_pvs.py` 364 L · `okbase_capture.py` 242 L ·
> `notify_provision.py` 238 L · `memstats.py` 187 L ·
> `operation_history_logic.py` 128 L · `secrets_util.py` 70 L ·
> tests: `test_okbase_menu.py` 895 L · `test_monitor_frozen.py` 608 L ·
> `test_alerting.py` 565 L · `test_rule_change_grace.py` 309 L ·
> `test_bot_commands.py` 243 L · `test_edge_cdp.py` 221 L ·
> `testing/` 10 test files + `bench_long_plot.py`

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
| `cpva_api.py` | Thin CPVA REST client, and `get_app_dir()` — every path in the program resolves through it. Also the shared request scheduler (`cpva_run_chunks`), the halve-on-refusal fetch (`cpva_fetch_samples_piecewise`, which hands each piece over rather than collecting it) and the 32 MB ceiling on a single answer. **No numpy in here**: `remote_launcher.py` imports `get_app_dir` from it and is built as its own always-on exe. |
| `chart_history.py` | Reading a long stretch of archive for a plot: measure how densely a channel is written, size the requests to match, condense every answer into per-point min/max/average as it arrives, and put into words what could not be read. Qt-free and matplotlib-free so it can be tested alone. See below. |
| `shared_pvs.py` | The PV list + shared settings on the scratch share. |
| `secrets_util.py` | Windows DPAPI encrypt/decrypt; `resolve_secret()` also understands `${ENV:NAME}`. |
| `notify_provision.py` | Bakes / reads `notify_provision.dat`, the channel settings shipped inside the build. |
| `bot_commands.py` | Grammar of the chat commands. Qt-free on purpose so it can be tested alone. |
| `okbase_menu.py` | The canteen menu behind `/food`: OKbase REST client, the `menu_cache.json` on the share, the `/food` word parser and the markdown it returns. Also the one owner of *reading a paste* (`read_paste`, `parse_curl`, `parse_cookies`, `describe_cookies`), so the script, the Settings dialog and the field all understand the same things. Qt-free and runnable alone (`python okbase_menu.py --check`, `--week`). |
| `edge_cdp.py` | The **Sign in with Edge** button: opens a visible Edge window on the OKbase page with a debug port and a profile of its own, waits for the person to sign in, and reads the cookies out of that window over the DevTools protocol. Includes a ~90-line websocket client because the stdlib has none. No new dependency, Qt-free, testable offline (`test_edge_cdp.py`). See below. |
| `okbase_capture.py` | The same job as the Settings buttons, from a shell: reads a "Copy as cURL" (or `--edge`) and saves the sign-in, ids and request template. A script, not part of the app — the fallback for when the app will not start. Its parsing now lives in `okbase_menu`. |
| `memstats.py` | Windows memory figures via `ctypes` (no psutil): this process's **commit** and working set, and the PC's commit charge/limit. Used by the status line, the half-hourly log line and the listener's console. |
| `remote_launcher.py` | Standalone always-on Webex listener that starts the app on `/run` and answers `/food` while the app is closed. Built as its own exe (`extra_exes` in `build_config.json`). |
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
  ├─ _check_limits_change        holds alerting after the rule in force changes
  ├─ alerting.AlertEvaluator     thresholds + debounce/settle + re-notify
  ├─ alerting.detect_frozen      "not updating"  (this PV's reading is dead)
  ├─ _check_refresh_health       "not refreshed" (this program stopped reading;
  │                              marks at once, notifies only if it lasts)
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
| Canteen sign-in (`okbase_*`) | `%APPDATA%\Diagnostic\okbase.json` | one person's credentials on one Windows account, so neither the share (a DPAPI blob decrypts nowhere else) nor the build (it is not everyone's) will do. **Not** `monitor_config.json` either: that sits beside the program, and the program has several homes — every rebuild would arrive with no sign-in. Overlaid by `load_config()` and stripped by `save_config()`, exactly like the provisioned channels |
| The canteen menu itself | `%APPDATA%\Diagnostic\menu_cache.json` **and** `Diagnostic\menu_cache.json` on the share | the menu is not a secret, and the always-on listener — possibly on another PC — answers `/food` from it while the app is closed |

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

### Conditional limits and the rule-change hold

`_match_profile(pv)` re-picks the threshold set in force on every poll: the first
profile whose `conds` all match the current dependency values, else the Global set;
`PVRuntime.dep_view` pins one by hand (`-1` = Global forced).

`_check_limits_change(pv, rt, now)` runs straight after, before the evaluator.
`_limits_key()` reduces the set in force to its four bounds — keyed on the numbers,
not the profile's index, so a duplicate rule or an edit elsewhere is not a change —
and a differing key opens a hold of `rule_change_grace_minutes` (Settings → "Hold
after a rule change (min)", default 20, 0 = off). While `rt.in_grace()`, `_on_poll`
skips the evaluator for that PV entirely; if the episode had announced nothing
(`alert.first_notified_ns == 0`) the `AlertState` is reset too, so the hold cannot be
followed by a recovery for an alarm nobody saw. An announced episode is kept, so its
recovery still goes out afterwards.

Why it exists: a chiller switched on in the morning. `PumpON` 1 puts the running band
(DA4: 10.8–12.0) in force while the water is at ~20 °C, and ten minutes of correct
cooling look like an alarm — every day.

**Not** what the 31.08.2026 19:10 messages were, though they looked like it. The
archive: rate 0.2 Hz and high power on all afternoon, tight band in force, Temp
11.2–11.5 — quiet. 19:01:40 `TempSP` 11.4 → 15.0; 19:05:08 `PumpON` 1 → 0 (chiller
off); Temp rises past 12.0 **with the running band still in force** → settle window
expires → ALARM 19:10:35. 19:10:17 `HighPowerStatus` 1 → 0 → rule stops matching →
Global 7–24 → `[OK] Recovered` 19:13:05. Nothing changed hands before that alarm, so
the grace cannot fix it: the band has to stop applying when the chiller stops running.
Done on 01.09.2026 — `L3-UTIL-CHL03-00N:PumpON = 1` is now a dependency of every
chiller rule (it is a change-only channel, but the archiver carries the last value
into an empty window, exactly as it does for `HighPowerStatus`, so it gates
reliably). On DA1–DA4 it *replaced* the hall state, which the shot-rate condition
already covers; on Helium and Utility, where the hall state was the only dependency,
it was *added* beside it — dropping it there would have let the tight running band
apply all night whenever the pump happens to run. The same pass widened the `3,3 Hz`
rate window to 3.0–3.5 on all four: DA1 asked for exactly 2.3 and DA2–DA4 for
2.0–3.0, so at the real 3.3 Hz none of them had ever matched.
`test_rule_change_grace.py` holds both: the start-up with the hold off, and that
evening still alarming.

The hold must not hide the reading: `PVRuntime.display_level()` reports the raw
severity while it runs (State cell still paints amber/red), `_alarm_status_text()`
shows `new limits → HH:MM` with `_grace_tooltip()` behind it, and `_status_line()` /
`_cmd_alarms()` add `— limits just changed, held until HH:MM`.

### "Not refreshed" — the program's own pulse

`_check_refresh_health()`, run from both the poll tick and the Webex listener tick
(two independent clocks), compares now against `_last_poll_ok_ns` — set only in
`_on_poll`, where a pass actually lands. Past `_refresh_limit_s()`
(`refresh_alarm_minutes`, Settings → "Mark as not refreshed after (min)", default
3.5 min, raised to the floor of five poll intervals + 30 s when set below it —
the floor keeps the marking longer than the wedge write-off in `_start_poll`, so
a stall that self-heals never shows) the program is not reading:

| Where | What it shows |
|---|---|
| status bar | leads with `⚠ NOT REFRESHED (last read HH:MM:SS)` |
| graph | red banner above it (`GraphPanel.set_stale_note`) — a QLabel, not figure text, so redraws and the blitted crosshair are untouched |
| State column | `not refreshed` for every row (`PVTableModel.stale`) |
| chat | **nothing for the first half hour.** It used to announce the stall the moment it saw it and then its recovery; the wedge watchdog cures most stalls seconds later, so the pair arrived together and said nothing. For a short stall the marking is the point. Past `refresh_alert_minutes` (Settings → "Say it in the chat after (min)", default 30, 0 = off, floored at `_refresh_limit_s()`) `_send_refresh_alert()` sends one message — measured from the last completed read, once only (`_refresh_alert_sent`), monitoring-armed only, no plot — and one more when reading resumes |
| `/status` | one short line above the list (`_refresh_note_short()`), `[not refreshed — …]` in place of `[ok]`, and `Values read at …` in the footer (present even when healthy) |
| `/alarms` | will not say "all clear" on out-of-date values |
| plots | `render_chart_png(stale_after_s=…)` stamps `NOT CURRENT` on the picture and repeats it in the message; only for windows that end at now |

`_pv_stale_note()` does the same for one PV, judged **at reply time** rather than
trusted from the last poll — when the poll is what stopped, the stored verdict is
stale too. It honours the same `frozen_check` opt-out.

---

## monitor_tab.py

### Data model

| Class | Role |
|-------|------|
| `PVConfig` | one configured PV: name, display name, thresholds, conditional rule profiles, valid range, group, `frozen_check`, on/off |
| `PVRuntime` | its live state: kept `samples`, last value, alert state, frozen info, and the rule-change hold (`limits_key`, `grace_until_ns`, `in_grace()`) |
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
| `_ChartWorker` + `render_chart_png` / `render_pv_png` / `_fetch_chart_data` | the PNG a chat `/plot` returns. The archiver half lives in `chart_history.py`; `_fetch_chart_data` is the seam the tests replace |
| `_CancelToken` | the switch behind `/cancel`. Set on the UI thread, read in the worker and inside `cpva_fetch_samples_chunked(cancel_fn=…)`, so the chunks not yet fetched are skipped. `MonitorWidget._chart_jobs` holds the tokens of the plots still running; a cancelled worker dispatches **nothing**, and `render_chart_png` sets `out_info["cancelled"]` so "no data in that window" and "you took it back" stay different answers |
| `_MeIdWorker`, `_CmdPollWorker`, `_TextReplyWorker` | the Webex bot loop |
| `_SharedWriteSignals` / `_shared_write_job` | the debounced share publish |
| `_MenuSignals` / `_menu_job(sig, settings, mode)` | the canteen menu, `mode` = `load` (saved copy) / `fetch` (read OKbase) / `ping` (keepalive). **A plain daemon thread, not the pool** — it writes the share, and a pool job stuck on a dead host would move the 48 s stall to app shutdown |

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
- **Bounded per plot**: the one thing that ever emptied this machine's memory was not a
  slow leak but a single `/plot` — see *The 24 GB plot* under `chart_history.py`. The
  half-hourly log line could not have caught it: `MEM_WARN_PCT` watches the **PC's**
  percentage, and 24 GB inside one process was still only ~75 % of the limit.
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
| `parse_time_spec(spec, now_ns, tz)` → `TimeRange` | `12h` / `90m` / `2d` / bare hours, `7-18`, `22-6` (crosses midnight), `today`, `yesterday`, `yesterday 7-18`, `15.8. 7-18`, `2026-08-15`, **and a range between two points: `1.1. 9:00 - 1.9. 12:00`, `1.1. - 1.9.`, `yesterday 21:00 - now`**. A window running past now is cut off and the reply says `(so far)`; a bare `15.8.` means the most recent 15 August |
| `parse_yaxis_spec(spec)` | `y 15-35` / `y auto` |
| `parse_plot_options(options, now_ns, …)` → `PlotOptions` | the `/plot` option list, including `detail` |
| `mention_help(bot_name)` | the tag reminder at the top of `/help` |

### The two-endpoint range

`parse_time_spec` tries the single-window forms first (`_parse_single_window`) and only
offers text they refuse to `_parse_range`; if that fails too, the **first** message is
the one raised. That ordering is what makes the addition safe: `7-18`, `22-6`,
`15.8. 7-18` and `2026-08-15 7-18` never reach the new code, and the `-` inside an ISO
date never has to be told apart from the `-` between two endpoints.

`_parse_range` tries every `-` in the text as a split point and takes the first one
where both sides parse as `[date] [time]` **and at least one side carries a date**. That
last condition is the whole safety net: without a date on either side the text is an
ordinary clock window, which the older parser already owns.

Three rules worth knowing:

- a side with no date takes the other side's date (`15.8. 9:00 - 22:00`);
- a side with a date but **no time** means the whole of that day, so `1.1. - 1.9.` ends
  at midnight after 1 September — the label still says `1 September, to the end of the
  day`, because nobody types `1.9.` expecting to read `2. 9. 00:00` back;
- a bare `5.9.` normally means the *most recent* 5 September, which for the far end of a
  range would land a year before the near end; when that happens the year is moved
  forward (`1.9. - 5.9.` typed on 2 September means the days that follow).

Refused: a range that ends before it starts (never silently rolled forward a day the way
a clock window is), one entirely in the future, and anything over three years.

PV names are matched loosely — any unique part of the display name or the PV name,
case-insensitive; an ambiguous or unknown name is reported and nothing is sent.

The mention Webex writes into the text is stripped in `monitor_tab._on_commands`
before parsing.

---

## chart_history.py — reading months of archive for one picture

The archiver has **no server-side averaging** (`count=` is accepted and ignored on this
server), so the only way to know what a channel did over six months is to read it. What
it refuses is a *response that carries too many readings* — measured somewhere above
~110 000 — not a long time range. A channel written once every ten seconds answers a
whole day in one request; a shot-energy channel does not manage two hours.

Asking for it an hour at a time, one channel after another, took **40 minutes for two
channels over 180 days**. The same question now takes about four.

| Step | What it does |
|------|--------------|
| `probe_rate(pv, start, end, …)` | up to **five** one-hour slices spread across the window (both ends included), run together, highest rate taken. Returns a `RateProbe`, not a bare number, because *how* it failed matters — see below. One probe is a coin flip on a facility that runs in shifts: a window starting at 03:00 on a Sunday would measure a rate a hundred times below the Tuesday-afternoon one, and every weekday request would then be refused. Never more than a quarter of the window is spent probing, the slices never overlap, and below a 6 h window it is skipped entirely |
| `plan_chunk_span(rate, …)` | how long one request should cover, aiming at 60 000 readings and snapped to a round span (1 min … 24 h). A chiller at ~3 700 readings/h gets 12 h, so 180 days costs **360 requests instead of 4320** |
| `plan_span_for(probe, …)` | the same, but from a `RateProbe`, so **"measured zero" and "could not measure" are told apart**. A quiet channel that answered still gets whole-day requests; a channel nothing could be learned about gets the 1 h span that is safe for anything |
| `_MemoryGuard` | asked before every request whether the read may go on: the caller's own cancel switch, plus this program's committed memory against `MEM_OWN_CEILING_BYTES` (4 GB) and the PC's against `MEM_PC_CEILING_PCT` (92 %). Latches — a read abandoned for memory never resumes — and sets `report.stopped_low_memory`, which `describe_fetch` turns into words |
| `plan_tasks(…)` | full coverage when it fits the request budget (600), everything when `detail` is set, otherwise one span-long stretch out of each of ~300 evenly spread buckets. The stretch touching the end of the window is always asked for **first** — the freshness stamp reads the newest reading, and a channel that is perfectly up to date must not be stamped `NOT CURRENT` because its last request happened to be scheduled last |
| `_BinReducer` | folds each answer into ~900 points (one per pixel) — count, sum, min, max, and the summed reading time — **in the thread that received it**, then drops it. A 60 000-reading answer is ~20 MB of dictionaries; collecting them all first is what runs the machine out of memory. `numpy` throughout: 98 000 readings condense in 0.03 s |
| `describe_fetch(…)` | the words for the chat reply and the stamp for the picture |

### The 24 GB plot, and the three limits that came out of it

On **2026-09-02** a 180-day plot froze the laptop: Windows' `Resource-Exhaustion-
Detector` (System log, **event 2004**, one every 5 minutes) names the process, and it
named `Diagnostic v1.1.2.exe` at 18.9 GB climbing to 23.9 GB of a 31.3 GB commit limit.
Three separate things had to be wrong at once, and all three are worth remembering
because each looked reasonable on its own:

1. **A rate of zero meant two different things.** Every probe hour of that window came
   back empty, `plan_chunk_span(0)` returned the *largest* span it had, and whole days
   were then asked for of a channel that is anything but quiet. Ignorance was planned
   as if it were a measurement. `plan_span_for` is that one line.
2. **Splitting made each answer smaller, not the result.** `cpva_fetch_samples_split`
   returns `left + right`: an oversized request is refused, halved, and every piece is
   *kept*, so a day of a busy channel costs the same memory however finely it is cut.
   With ten requests in flight that is where the gigabytes came from.
   `cpva_fetch_samples_piecewise` hands each piece to `on_piece(start, end, samples)`
   and forgets it, so a range now costs what its **largest piece** costs.
   `cpva_run_chunks` re-cuts the `ChunkTask` to each piece's own bounds, so the
   read-coverage bookkeeping stays right; anything collecting results must key them by
   `(index, start_ns)`, since one planned request can arrive as many pieces.
3. **Nothing capped an answer.** `resp.content` held whatever the archiver agreed to
   send. `_http_get_json` now streams and abandons the download past
   `MAX_RESPONSE_BYTES` (32 MB), raising `ResponseTooLarge` — which
   `cpva_is_splittable_error` treats exactly like the archiver's own refusal, so the
   existing halving and `span_hint` machinery corrects the plan by itself.

Why 32 MB: a reading is not a number and a timestamp. It carries severity, status,
quality and a `metaData` block — measured **345 B on the wire and ~1.7 KB in RAM once
parsed**, a five-fold expansion. 32 MB is ~93 000 readings (~160 MB parsed), above the
60 000 a request is planned for and just above the archiver's own ~110 000 refusal, so
an ordinary plot never trips it and a wrong plan cannot cost more than ~1.6 GB across
ten workers. `SPLIT_MAX_DEPTH` is 8, not 4: four halvings only get a whole-day request
down to 1.5 h, which a busy channel still cannot answer, and the chunk was then given
up on and left a hole in the picture.

`_MemoryGuard` is the backstop behind all three, not the fix — if a plan is wrong in
some way nobody foresaw, the plot abandons itself, draws what it read, and says so.

### Three kinds of blank, and why they are worded differently

A gap in the curve can mean three things, and they look identical on the picture:

- **read, and the archiver holds nothing** — `bin_read_ns > 0`, `bin_count == 0`;
- **could not be read** — the request failed; `bin_read_ns` stays 0 and `failed_ns` grows;
- **never asked for** — sampling skipped that stretch; also `bin_read_ns == 0`, but
  `n_chunks_failed` is 0.

Reporting the second as the first is the failure this module exists to prevent: the old
code answered all three, and a crash, with `No archived data in that window`.

### Numbers that were measured, not guessed

Against `L3-UTIL-CHL03-001:Temp` on 2026-09-02:

| | |
|---|---|
| one request, 12 h ≈ 49 k readings | 1.3 s HTTP, 0.64 s JSON, **0.03 s** to condense |
| one request, 24 h ≈ 99 k readings | **9.3 s** HTTP — the server falls off a cliff, which is why the target is 60 k |
| 6 / 10 / 16 / 24 requests in flight | 66 / 91 / 99 / 87 k readings per second — it saturates, so `plot_max_workers` is 10 and more would not help |
| 180 days, 2 chillers | 255 s, 26 M readings, 83 % of the window |
| one reading | **345 B** on the wire, **~1.7 KB** in RAM once parsed |
| 180 days, 1 chiller, after the size ceiling | 360 requests, 186 s, 16 M readings, 100 % of the window, **+269 MB** peak memory |
| the same read over 7 days | +178 MB peak — memory follows the request size, not the window |

`plot_max_workers` is also kept well below `poll_max_workers` on purpose: a plot can now
run for minutes and shares both the archiver and the 64-connection pool with the live
poll. Let it take too many and a poll pass overruns its interval, which posts a
*"not refreshed"* alert to everyone — an alarm caused by drawing a graph.

### What the picture looks like

A window short enough to hold every reading (≤ 20 000) is drawn exactly as it always
was: one step curve through the readings. A longer one is drawn as the average of each
point, with a shaded band and thin edge lines from its lowest to its highest reading.
The edge lines matter: a one-off excursion lands in a single point, and a single point of
pale fill is one invisible pixel — which is exactly the reading somebody is looking for
in a six-month plot. Above three curves the bands are left out (they turn to mud) and the
caption says so.

---

## okbase_menu.py — the canteen menu behind `/food`

Qt-free, `requests` only, and **nothing in it raises**: every function that touches
the network or the disk returns a value plus an error string, the same discipline
`alerting.WebexNotifier.send` follows. A broken portal must cost a menu, not the bot.

| Symbol | Role |
|--------|------|
| `parse_menu(payload)` | `{'2026-08-27': [Meal, …]}` out of whatever the portal returned |
| `session_from_password(user, pwd, …)` / `session_from_cookie(cookies, …)` | the two ways in; `is_signed_in()` probes `/rest/app-info/serverovy-cas`, which answers 401 while signed out |
| `parse_cookies(text)` | a whole browser `Cookie:` line, one `JSESSIONID=…` pair, or a bare id. Keeps **every** cookie: a remember-me cookie among them is what lets `/rest/authentication/remember-me` mint a new session, so the paste outlives many session timeouts |
| `filter_candidates(...)` / `retarget_filter(...)` / `fetch_menu(...)` | the request bodies to try, the stored one re-pointed at today's dates, and the POST that keeps whichever worked |
| `fetch_weeks(session, first_monday, weeks)` | **the one to call**: the portal returns a single week per request, so this asks per week and merges |
| `open_session(settings)` | sign in however this PC can — password first, then the pasted cookie. The one place the stored secrets are resolved |
| `refresh(settings, session_out=…)` | the whole job: sign in, fetch two weeks, write the cache. `session_out` hands back the cookie line so the caller can save a rotated session |
| `keepalive(settings)` / `cookie_header(session)` | one cheap request that stops a borrowed session idling out, and the cookie line to store afterwards |
| `render_status(cache)` | the `/food status` answer the listener can give without the app |
| `local_cache_path()` / `shared_cache_path(...)` / `read_cache` / `write_cache` / `load_cache(settings)` | `menu_cache.json` in `%APPDATA%\Diagnostic` **and** next to the shared PV list; the newer of the two wins |
| `OKBASE_KEYS` / `user_settings_path()` / `load_user_settings()` / `save_user_settings(values)` | the sign-in, in `%APPDATA%\Diagnostic\okbase.json` — the one file every build, the source run and the listener all read, so a rebuild inherits it instead of arriving blank. `save_user_settings` **merges**: the Settings dialog and a background session renewal are two writers |
| `parse_food_args(args)` / `answer_food(args, cache)` | the `/food` words, and the markdown reply. `;` and `,` are treated as spacing, and a language word (`cz` / `en`) may sit anywhere in the line |
| `next_food_day(cache, now)` / `LUNCH_OVER_AT` | which day a **bare** `/food` means: today until 14:30, then the next day the saved menu has meals on — Friday afternoon lands on Monday, not on an empty Saturday. `FoodRequest.default_day` is what marks the request as free to move; a named day never is |
| `split_languages(name)` | the Czech and English halves of one field. Decided by **diacritics, not position** — the order is not reliable — and left whole when both halves look Czech, because `"Řízek vepřový/kuřecí"` is one dish |
| `render_meals` / `render_day` / `render_week` | grouped by course under a heading each, numbered within the group. The course used to be a trailing label, which put the word "soup" where the eye looks for the price |

**The portal.** `elieric.okbase.cz` is an Angular front end over a JSON REST API, so
this is an API client and **not** a scraper — no HTML parsing, no new dependency.
Verified endpoints (base `…/okbase/service`):

```
POST /rest/stravovani/objednavky/nacti-vse      the meal list
GET  /rest/app-info/serverovy-cas               401 unless signed in
POST /rest/authentication/manual                username + password
GET  /rest/authentication/web-login-config      which sign-ins are enabled
```

`web-login-config` on this instance answers `ssoEnabled: "SAML", ssoDefault: true,
loginFormVisible: true` — single sign-on through Microsoft Entra ID is the normal
way in, and the local form is still offered.

**The sign-in is the hard part, and no code will fix it.** This site's SSO asks for
a confirmation in the authenticator, so there is no unattended way in at all: a
second factor exists precisely to stop one. Hence `session_from_cookie` — the
operator pastes the browser's own `Cookie` line into Settings, and the program
borrows a sign-in a human already completed. `parse_cookies` therefore keeps every
cookie, not just `JSESSIONID`, and an expired session is retried through
`/rest/authentication/remember-me` before being given up on.

**How the borrowing is done, and what was rejected.** The copying by hand is now
the fallback, not the route: Settings → Canteen menu has **Sign in with Edge**
(`edge_cdp.py`) and **Paste sign-in from clipboard**. The rejected alternatives
are worth keeping written down, because each of them looks cheaper than it is:

| Rejected | Why |
|---|---|
| a **headless** browser | ~150 MB into the build and it still stops at the authenticator prompt — a second factor exists precisely to stop an unattended sign-in |
| an **embedded** browser window (Qt WebEngine) — which *would* clear the prompt, because a human is looking at it | measured on PySide6 6.11.1: `Qt6WebEngineCore.dll` alone is **195.3 MiB**, and with resources and locales it is +237-280 MiB on a 302 MiB **shared** `_internal` that is copied next to every program in the repo. And it is a bare Chromium with no Edge account plumbing, so every renewal would be a full Microsoft login *with* the phone tap — worse than driving Edge, which on this PC may need no prompt at all |
| reading **Edge's own cookie store** | cannot work. Edge here is 152, long past the 127 cutoff where App-Bound Encryption tied the cookie key to the browser's own identity; getting past it means injecting into Edge, which is the technique infostealer malware uses, breaks on Edge's four-week cadence, and would be the worst thing an audit could find in this repository |
| a **bookmarklet** posting the cookie to a small local listener | dead twice over. Shibboleth sets `_shibsession_` `HttpOnly` and Tomcat sets it on `JSESSIONID`, so `document.cookie` returns neither cookie that matters — that is what `HttpOnly` is *for*. And Edge policy on this PC allows a page to fetch `127.0.0.1` only from two SharePoint origins |
| a browser **extension** (which can read HttpOnly cookies) | kept as the designated fallback if the DevTools route is ever locked down. Not first: an unpacked, per-profile, unversioned manual install, plus a permanently listening socket inside Diagnostic |
| **MSAL** / a silent Windows token exchanged for a portal session | a Shibboleth SP does not accept tokens. It accepts a signed SAML Response delivered by a browser POST and validates it against request state it planted in the browser — the dozen `_opensaml_req_ss…` cookies in a real capture *are* that state. There is no supported OIDC→SAML2 exchange, SAML ECP has never been implemented by Entra as an IdP, and it would need an app registration and admin consent: a ticket, not a code change |
| an **OKbase-local password** or a service account | asked for and refused. `session_from_password` stays as documented dead code, and is no longer tried first (see below) |
| putting a cookie **through Webex**, in or out | a room is a shared place with permanent history, and a `_shibsession_` value is not a hint about a credential — it *is* one, good for the whole HR portal. Webex may carry the request to act, never the secret. That rules out a `/food signin <cookie>` command, a DM flow, and echoing a cookie in `/food status` |

### Sign in with Edge — `edge_cdp.py`

Opens a **visible** Edge window on `MENU_PAGE_DEFAULT`, waits for the person to
sign in, and reads the cookies out of that window over the DevTools protocol.
What it hands back is a plain `Cookie:` line — the exact text
`parse_cookies` has always eaten — which is why nothing downstream changed.

Four details that are load-bearing, and each of which silently breaks the whole
thing if it is "tidied up":

- **A profile of its own** (`--user-data-dir`, under `LOCALAPPDATA`). Not a
  nicety: since Chrome/Edge 136 the debug port is **ignored** without a
  non-default one. It is also what keeps the operator's real Edge untouched,
  unrestarted and unclosed. LOCALAPPDATA rather than the `%APPDATA%\Diagnostic`
  the rest of this program uses, because a browser profile is hundreds of
  megabytes of cache and must not roam.
- **Never headless.** The window IS the feature. `launch_args` is its own
  function so a test can assert this.
- **A random port**, and the window closed the moment `_shibsession_…` appears.
  While it is open, that port will hand its cookies to anything running as this
  Windows account, so it is open for seconds rather than hours.
- **A hand-written websocket client** (~90 lines), because the stdlib has none
  and one DevTools command is not worth a dependency in the shared bundle. It
  handles fragmented messages and pings on purpose: a cookie list is big enough
  to arrive in pieces, and Chromium does ping. `Storage.getCookies` is the
  method that answers on the browser target — measured 2026-09-02;
  `Network.getAllCookies` answers *"wasn't found"* there and is kept only as a
  fallback for another build.

Verified end to end on 2026-09-02: the debug port answered, `Storage.getCookies`
returned the profile's cookies, and the line it produced round-tripped through
`parse_cookies`. `test_edge_cdp.py` covers the framing, the domain filter and the
flags offline — no browser, no network.

#### The two pages it walks past by itself (2026-09-03)

As first written, opening the window was all it did — and measured against the
real portal it never finished on its own. It stalled twice, both times on a page
that only looks like progress:

1. **The portal's own sign-in page.** A caller with no session asking for the
   menu page is bounced to `web-client/login?…`, which shows a form this account
   cannot use plus a *company account* button that has to be clicked. So the tab
   is walked to the address that button leads to — `sso_start_url()`,
   `…/web-client/web?sso=yes&dataSource=defaultDataSource&organization=1` — which
   is the portal's own route and not a guess. That starts the SAML redirect.
2. **Microsoft's "Pick an account".** With two work accounts signed in on the PC
   — the normal case here — this is asked *every* time, and only one of the two
   is the account the portal knows; the other signs in to a tenant that has never
   heard of the canteen. `pick_account()` clicks the tile carrying the address
   from the new **Work account** setting (`okbase_account`). It clicks the
   tightest element whose text holds that address and lets the click bubble to
   the row's own handler, so Microsoft's markup does not have to be guessed at.

Each is done once per landing, so a page that ignores it is not hammered, and
`progress` now says which of the two is happening. Anything after that — a
password, an authenticator prompt — is still the person's to answer, which is why
the window is visible and is brought to the front (`bring_to_front`) when the
walk cannot go further. Timed on this PC 2026-09-03: **5 s from button to saved
sign-in, no clicks at all**; before the walk it timed out at 300 s on step 1.

`renew()`'s timeout error now names the page the window was left on. "The
sign-in was not completed in time" on its own reads as a broken program, when
almost always it is a prompt sitting unanswered in a window nobody looked at.
`testing/probe_edge_signin.py` prints the same walk with a stopwatch, and
`testing/probe_edge_page_text.py` prints the words on the page it is stuck on —
that is what identified both stalls.

Every *other* page now names itself too — `progress` says "the window is on
&lt;site&gt;" whenever the tab moves. Those two pages are the only ones a program can
walk past; on anything else the window is simply waiting for the person, and
saying nothing about it is exactly what "it does not know where it is supposed
to sign in" looks like from the outside.

#### The window itself, and the three ways it fails (2026-09-04)

The walk above assumes there IS a window. Measured against the live portal, all
three of these came back within seconds and two of them said the wrong thing:

- **A browser was already open on this profile.** Then a second Edge is not a
  second browser: Windows hands it the address and the new process exits inside
  two seconds, so the debug port on the *new* random port never opens. That was
  read as "could not talk to the browser" — after the full 30 s port wait, for a
  situation nothing about the browser was wrong with. `browser_socket_url` now
  watches the process it started, ends the wait the moment it quits, and
  `renew()` **repairs it**: `close_profile_edges()` closes the leftover and the
  launch is done again. Measured: detected at 2.2 s, signing in again by 12 s.
  Safe by construction — the profile belongs to this feature alone, so no window
  of the operator's can be closed by it.
- **The window was closed by the person.** A real, ordinary ending, and the only
  one of the three that deserves "the sign-in was not completed".
- **Edge moved its own work to a fresh process.** A browser is allowed to do
  that; the port stays open and the window stays on screen. `renew()` used to
  test `proc.poll()` FIRST, so the pid going away was read as a closed window
  and the sign-in ended instantly — with the window still sitting there. **The
  port is the test for "is the window still there", never the process id**
  (`browser_is_up`). The pid is now watched for one thing only: ending the
  port wait early in the first case above.

`testing/probe_edge_launch.py` is what these were measured with — it prints,
twice a second, whether the launched process is alive, whether the port answers
and where the tab is, for the first seconds of a launch.

### Two kinds of failure, and why they must never share a message

`session_state()` answers **alive / signed-out / unreachable**, and only 401 and
403 mean signed out. Everything else that goes wrong — a 502 from the proxy, a
dropped VPN, a read that timed out — is `PORTAL_UNREACHABLE`, never
`SESSION_EXPIRED`.

This is not tidiness. It was the actual bug: `is_signed_in` returned a bare
`False` for all of them, `session_from_cookie` then walked `REVIVE_PATHS`, every
GET raised, and the operator was told **"the canteen sign-in has expired"** — so
they went to the browser and pasted in a sign-in that was working perfectly. On
2026-09-02 the app was saying exactly that while the same saved cookie read the
menu on the first try. Classify through `is_expired()` / `is_unreachable()`; never
compare the strings, and never test for the word "expired" (the listener used to,
and the unreachable message carries its reason on the end).

Three consequences worth knowing:

- an unreachable answer does **not** attempt a revive — those paths live on the
  host that just failed to answer, so it is three more timeouts for a certain
  nothing;
- `_menu_last_decisive` in the tab is the last verdict that actually settled the
  question, and it is what `/food status` and the status line quote. An
  unreachable tick records only that it could not tell;
- `open_session` now tries the **cookie first**. On this site the password form
  cannot work at all, so trying it first spent up to four POSTs and four probes —
  160 s at the default timeout — at the front of every attempt, before touching
  the sign-in that does work.

### The program must not destroy its own sign-in

`merged_cookie_header(session, previous)` replaced a plain `cookie_header` at
every point where a renewed session is written back. `requests` drops a cookie
the instant the server sends a deleting `Set-Cookie`, and a Shibboleth SP does
exactly that when it decides a session is invalid — so the shortened line was
being saved straight over the stored one, **without `_shibsession_…`**, throwing
away the one thing that can mint a new session and the one thing a re-paste
exists to supply. New and changed values win; a name that vanished is kept.

### Answering "do I have to paste it in again?" in five seconds

`python okbase_menu.py --check` opens the stored sign-in and says alive,
signed-out or unreachable, plus what the saved menu covers. Cookie names and
lengths only, never values, so the answer can be screenshot.

It exists because the question kept being answered by guessing, and the guess was
always "I had better paste it again". **A rebuild does not lose the sign-in**: the
canteen keys live in `%APPDATA%\Diagnostic\okbase.json` (`OKBASE_KEYS`) and every
build, the source run and the listener read that same file. `_cli_settings` now
overlays it too — without that, the shell commands were reading a stale copy out
of `monitor_config.json` and could report "no sign-in" while the app was signed
in perfectly well.

**The session cookie is disposable; the SSO cookie is the asset.** Measured
against the live portal on 2026-08-26: with `JSESSIONID` deleted — and again with
a deliberately dead one — a GET of `/rest/authentication/sso` returned **200 and
a brand new JSESSIONID**, with no redirect to Microsoft and no authenticator
prompt, purely on the strength of the `_shibsession_…` cookie that came with the
paste. `session_from_cookie` therefore walks `REVIVE_PATHS` before ever reporting
an expiry. This is what turns "paste it in every morning" into "paste it in when
the Shibboleth session finally lapses", and it is why `parse_cookies` must keep
every cookie rather than picking out `JSESSIONID`.

The keepalive still earns its place: one cheap `serverovy-cas` request every
`okbase_keepalive_min` (10 min) keeps both sessions from lapsing through disuse
in the first place, so the revive path stays a fallback rather than the norm.
`_menu_tick` runs at the keepalive rate while still fetching the menu only once a
calendar day. Whatever cookies the portal last handed out are saved back over the
stored ones (`_remember_session` in the app, `_save_okbase_cookies` in the
listener) — a revived session has a NEW id, and without saving it the next
keepalive would keep poking a session that no longer exists.

When the browser session finally lapses past the point the revive path can
recover, a person re-pastes it (Settings, or `okbase_capture.py`) into the
Windows account's own file. A long-running app held its own copy from launch, so
that paste used to need an app restart to take effect. `_reload_okbase_sign_in`
removes that: `_menu_tick` re-reads the file before every keepalive, and
`/food refresh` before its on-demand fetch, so a fresh paste heals the running
app within one keepalive interval — no restart. It is announced once ("picked up
a renewed OKbase sign-in") and re-arms the expired-once notice.

Saving the Settings dialog does not wait for that interval: it clears the old
verdict (`_menu_error`), re-arms the clock with `_arm_menu_timer()` and fetches
at once. It deliberately does **not** call `_start_menu_watch()` — that queues a
"load" job, and since only one menu job runs at a time the fetch that is the
whole point of having just typed the credentials in would be dropped silently.
That single job slot has a watchdog for the same reason: the thread cannot be
cancelled, so a job that never answers would leave `_menu_busy` set for the rest
of the run and every later attempt — including a freshly pasted sign-in — would
be refused without a word, with `/food` repeating the last verdict for ever.
After `MENU_JOB_WEDGE_S` the missing job is written off, in the log, and a new
one starts.

**The always-on listener needed the same treatment, and it was worse off.**
`remote_launcher.main()` read its settings **once** and handed that same dict to
`_menu_upkeep` and `_food_answer`, so a re-paste was invisible to it until the
listener was restarted. And `_save_okbase_cookies` wrote a renewed session to
`okbase.json` without updating the dict, so the process renewed a session, saved
it, and then went straight back to using the original paste on the next tick — it
limped along only because the revive path works, and died the moment that
stopped. `_reload_okbase_settings(settings)` fixes both: it updates the dict **in
place** (never a rebinding — `main()` holds that exact object), runs once per
keepalive behind the interval gate, and again just before answering a `/food` so
a sign-in pasted a minute ago is used at once. Only `okbase.json` is re-read: the
bot token and the room do not change under a running listener.

**Nothing about the canteen is ever announced in the chat** (changed
2026-09-03). It used to post "the canteen sign-in has expired" once per
breakage, from the tab *and* from the listener. The menu is a convenience nobody
is waiting on, so an unprompted warning about it is noise in a room that exists
for laser alarms. An expired session is now said only where somebody asked:
`_menu_signin_note()` adds one italic footer line to a whole-list `/status`,
`/food` marks it under the menu, and `/food status` reports the whole picture.
All three read `_menu_error` through `is_expired()`, so a portal that merely did
not answer says nothing — see the two-kinds-of-failure section above. Keepalive
results still reach the window log **only when the outcome changes**, or one
line every ten minutes would bury it.

**The Settings buttons never run on the UI thread.** Both canteen sign-in buttons
and "Read the menu now" go through `_OkbaseSignInSignals` + a plain daemon thread
(`_okbase_signin_job`, `_okbase_verify_job`). One of them waits minutes on a
person at a Microsoft prompt and the other on a portal that may be slow; the
dialog is modal, and a modal window that stops repainting is what Windows draws
as "Not Responding". A plain thread rather than `QThreadPool` for the same reason
`_menu_job` uses one — the pool waits for its runnables when it is destroyed,
which would move that wait to closing the program. The verify job passes
`write=False`: it runs on details the operator may still cancel, so it must not
overwrite the saved menu, the saved sign-in or the remembered request template.

`/service/v3/api-docs` is compiled in but **broken on this instance**, and there
are no public OKbase API docs. The request was therefore **captured from the
portal's own front end** (2026-08-26, DevTools → Network → `nacti-vse` → Copy as
cURL), which settled two things guessing never would have:

```
x-okbase-datasource: defaultDataSource      ── all three are REQUIRED. One OKbase
x-okbase-language:   cs                        server hosts several organisations
x-okbase-org-id:     1                         and data sources, so a call without
                                               them asks a different question.
{"datumOd":"2026-07-27T00:00:00.000+02:00",   ── the body. datumOd/datumDo is a
 "datumDo":"2026-09-06T00:00:00.000+02:00",      ~6-week range; weekStart/weekEnd
 "weekStart":"2026-08-24T00:00:00.000+02:00",    is the week being DISPLAYED
 "weekEnd":"2026-08-30T00:00:00.000+02:00",      inside it — hence `week_of`,
 "userId":6507822,"jidelnaId":1,                 which is not day_from.
 "objednavky":{}}
```

`filter_candidates()` leads with that exact shape and keeps looser fallbacks
behind it. A body captured on a particular PC is stored in `okbase_filter` as a
**template** and always passed through `retarget_filter()` before being sent —
replaying it verbatim would ask for the week it was captured in for ever, which
is the one failure mode where a working capture still returns the wrong menu.
`FETCH_DAYS_BACK/AHEAD` (28/13) mirror the browser's own range on purpose:
asking narrower than the front end ever asks risks getting only the displayed
week back. `userId` and `jidelnaId` live in settings, never in the code.

**One request returns ONE week** — the `weekStart`/`weekEnd` one, regardless of
how wide `datumOd`/`datumDo` is. Measured, after a wide single request cheerfully
returned the menu for a month earlier. Hence `fetch_weeks()`, which asks per week
(`FETCH_WEEKS` = 2: this week so `/food` works, next week so `/food next week`
does) and merges. This is the failure mode to remember: it looks like success.

The answer's real shape, confirmed live:

```
{"listky":     {"2026-08-26": {stav, jidelnaId, svatek, polozky: [
                    {id, poradi, jidlo: {nazev, typ:"POLEVKA", popis:"9",
                                         aktualniCenaPlna, aktualniCenaDotovana,
                                         kategorie:[{textCs, organizace:{nazev}}]}}]}},
 "objednavky": {"2026-08-26": [ …the person's own orders, every nazev null… ]}}
```

Three traps in that one object, each covered by a test:

* the price is **`aktualniCenaDotovana`**, the subsidised one people actually pay
  — `aktualniCenaPlna` is nearly three times as much on some days;
* `popis` is the **allergen numbers** ("9"), not a description, so it is not a
  name key — otherwise a meal missing its `nazev` would be listed as "9";
* `kategorie[].organizace.nazev` is "ELI ERIC", so one careless recursion serves
  the institute for lunch. `kategorie` is in `_KIND_KEYS`, and every kind / price
  / date key is in `_NO_RECURSE` — they are values, never containers.

Around those, the answer is read as a **tolerant walk** rather than against a
fixed schema: field names are matched case- and underscore-blind, dates are
accepted in four spellings plus epoch millis, and `typ` codes go through
`_pretty_kind` so an added course reads as words rather than SHOUTED_SNAKE_CASE.

Cookies seen in a real capture: `JSESSIONID`, a `_shibsession_…` **Shibboleth**
SP session (so the SSO is Shibboleth/OpenSAML, not Spring SAML), and a dozen
transient `_opensaml_req_ss…` request-state cookies. There is **no** remember-me
cookie here — the Shibboleth session is what can outlive `JSESSIONID`, and it is
kept because `parse_cookies` keeps everything.

**Why the cache is the point.** The menu changes at most once a day, so answering
`/food` needs no live session. Only `refresh()` needs credentials, and those are
DPAPI blobs belonging to one Windows account on one PC. So the app fetches and
writes `menu_cache.json`; both `/food` handlers only ever read it, which is how the
command still answers with the app closed.

**Freshness is judged when answering, not when fetching** (the house rule): a cache
older than `STALE_AFTER_HOURS` gets a bold warning above the menu, and a day outside
the fetched span is reported as unknown — never replaced by another day's food.

---

## remote_launcher.py — the always-on listener

Qt-free, imports only `alerting.WebexNotifier`, so it stays a lightweight background
process. Built `--onefile` as its own exe (see the Dev Tools STRUCTURE for why).

| Function | Role |
|----------|------|
| `_split_command(raw)` | strips the mention, then returns `(first word, the rest)` — only the **first word** has to be the command, and `/food week` needs its argument. (Fixed 2026-08-19: it used to compare the whole message with `/rundiagnostic`, so the tag Webex writes made it never match — the command looked ignored in exactly the kind of room where the tag is compulsory.) |
| `_food_answer(args, settings)` | the `/food` reply, from the saved `menu_cache.json`. `okbase_menu` is imported **inside** the function, so a problem there can never stop this listener doing its main job. Answered only while the app is **closed** — `is_app_running()` gates it, or every menu would be posted twice |
| `_menu_upkeep(settings, state)` / `_save_okbase_cookies(cookies)` | keeps the OKbase sign-in alive and reads the menu once a day **while the app is closed**. This is what stops a pasted browser session expiring overnight: this is the process that is always up. Gated on `is_app_running()` for the same reason `/food` is, plus one more — both writing `okbase_session_cookie` would let the app's older in-memory copy overwrite a renewed one |
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

`/food` is the **opposite** case and is deliberately answered by only one of them.
Both processes can render it from the same `menu_cache.json`, so the listener checks
`is_app_running()` and stays silent while the app is up — a menu posted twice is
noise, where a duplicated `/run` answer is merely redundant.

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
python test_monitor_frozen.py   the two "not live" checks as the tab uses them:
                                "not updating" (verdict, one-shot notification,
                                table rendering) and the refresh watchdog
                                (/status, /alarms, State column, chart stamp,
                                and when a lasting stall reaches the chat)
python -m pytest test_rule_change_grace.py
                                the hold after the limits in force change: when
                                it opens, what it does to a half-built alert
                                state, what the table and the bot say meanwhile,
                                and the 31.08. chiller evening as a regression
python test_bot_commands.py     the chat command grammar
python test_okbase_menu.py      the canteen menu, pinned against a live
                                capture: the real payload and its three
                                traps, splitting the two languages out of
                                one field, grouping by course, reviving a
                                dead session through single sign-on, the
                                /food words, that a bare /food moves to the
                                next serving day after 14:30, that an
                                unreachable portal is never called an
                                expired sign-in, that a renewed cookie line
                                never loses the company sign-on, reading a
                                pasted cURL in both flavours, and that a day
                                the cache cannot answer for says so instead
                                of showing another day's food
python -m pytest test_edge_cdp.py
                                the Sign in with Edge plumbing, offline: the
                                hand-written websocket framing (masking, the
                                three length forms, fragments, pings, a
                                message arriving in dribbles, an event that
                                is not the answer), that only the portal's
                                own cookies come through, that the line it
                                builds round-trips through parse_cookies,
                                and that the launch flags never go headless
                                and never lose --user-data-dir
python testing/test_cancel_plot.py
                                /cancel: the chunks still queued are never
                                fetched, a cancelled fetch answers with
                                nothing rather than a part of the window,
                                the renderer says WHY it came back empty,
                                and a cancelled job sends no message
python testing/test_time_ranges.py
                                the time-window language, starting with a
                                regression table of every form that already
                                worked, then the two-point range, the year
                                inference, and the two daylight-saving days
python testing/test_fetch_isolation.py
                                a refused request is halved until it fits, a
                                404 is not, one bad chunk no longer costs the
                                whole channel, and one bad channel no longer
                                costs the other curve
python testing/test_chunk_plan.py
                                request sizing from the measured rate, full
                                coverage without holes or overlaps, the even
                                spread when it samples, and the refusal when
                                the question cannot be answered well
python testing/test_bucket_reduce.py
                                condensing: min/max/average per point, a spike
                                surviving it, out-of-range readings dropped,
                                read-but-empty told apart from never-read, and
                                nanosecond precision over half a year
python testing/test_plot_message.py
                                the words: a failure is never reported as an
                                empty archive, and an empty archive is never
                                reported as a failure
python testing/test_chart_render.py
                                the picture itself: it comes out, one dead PV
                                does not take the other curve with it, and the
                                SAMPLED / INCOMPLETE stamps appear
python testing/test_response_ceiling.py
                                the 24 GB plot, both halves: an oversized
                                answer is abandoned mid-download (and one that
                                announces its size is never started), and a
                                split range arrives piece by piece instead of
                                as one pile — with its pieces covering the
                                range exactly once, one unreadable piece not
                                costing the others, and no readings lost when
                                several pieces share a chunk index
python testing/test_probe_honesty.py
                                "measured zero" against "could not measure":
                                a quiet channel still gets whole-day requests,
                                an unmeasurable one gets an hour, a probe too
                                big to read counts as dense, and measuring
                                never eats a short window
python testing/test_memory_guard.py
                                the backstop: what trips it (this program's
                                own commit, and the PC's), that it latches,
                                that giving up is not reported as a memory
                                problem, that the figures are not read on
                                every request, and that a runaway plot stops
                                itself, keeps what it read, and says so
```

Not a test — a measurement against the live archiver:

```
python testing/bench_long_plot.py --days 180
                                reads a real window and prints the plan, the
                                requests actually made, the biggest answer
                                held and the peak memory. `--force-span-h 24`
                                reproduces the plan that froze the laptop.
                                A peak that does not grow with --days is the
                                size ceiling working
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
