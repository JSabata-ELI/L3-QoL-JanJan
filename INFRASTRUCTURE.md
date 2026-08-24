# L3 QoL tools — INFRASTRUCTURE

> Verified against source: 2026-08-19

What every program in this repository shares: the machines and shares it talks to,
how a source folder becomes a program on a lab PC, where each program keeps its
settings, and which documents describe what.

Per-program detail lives in each folder's own `STRUCTURE.md`. This file is only
about what crosses folder boundaries.

---

## 1. The programs

| Folder | What it is | Launcher group | Toolkit | Built? |
|--------|-----------|----------------|---------|--------|
| `Image Tools` | Camera images: Finder, Slider, Shot Finder, Workshop | Scripts | PySide6 | yes |
| `Screenshots` | Camera frames + monitor shots into one folder | Scripts | tkinter | yes |
| `Time Converter` | Rename archive files to readable timestamps | Scripts | tkinter | yes |
| `Announcer` | Screen-region watch + PV badges | Scripts | tkinter | yes |
| `CSS Logger` | Archive explorer / logger (**Spectra** is its second tab, `sp_t.py`) | Scripts | PySide6 | yes |
| `Chiller Log` | Long-term chiller flow/temperature trend | Scripts | tkinter | not yet |
| `Diagnostic` | Live PV monitoring, alerting, Webex bot | External¹ | PySide6 | yes (+ helper exe) |
| `Pulser Monitor` | Diode-array pulser analysis from camera frames | External¹ | PySide6 | yes |
| `Shift planner` | Fill your availability into the shift spreadsheet | External¹ | tkinter | yes |
| `Calibrations` | Energy-detector calibration | In progress | PySide6 | yes |
| `Launcher` | Starts everything else | Parts | tkinter | yes |
| `Dev Tools` | Builder + Copy Manager (build and publish) | Personal | tkinter | yes |
| `Internal Builder` | Builds the shared support folder | Personal | — | yes (by-product) |
| `Extractor` | Manual fallback for distributing the support folder | External¹ | console | no, on purpose |
| `Git Work` | Everyday git from one window | Personal | PySide6 | yes |

¹ **Not classified in the Launcher.** `Launcher/l.py` lists the group membership in
`SCRIPTS` / `PARTS` / `IN_PROGRESS` / `NOT_WORKING_CORRECTLY` / `PERSONAL`; anything
listed in `SUBSUMED` (Image Finder, Image Slider, Shot finder, Spectra, Builder,
Copy manager) is skipped entirely, because each is now a tab of a bigger program and
the folder on the share only holds an old standalone build of it; anything
absent falls into **External**. Diagnostic, Pulser Monitor, Shift planner and
Extractor are currently absent, so they show up there. That is a gap in the list, not
a statement about the programs — right-clicking a card moves it per user, but the
permanent fix is to add the folder name to the right set.

`SCRIPTS` also contains `"Chiller log"`, which normalises to match the real folder, so
Chiller Log will appear under Scripts as soon as it is built.

### Where each program's icon and build settings stand

| | has `icon.ico` | has `build_config.json` |
|---|---|---|
| Announcer, CSS Logger, Diagnostic, Image Tools, Internal Builder | yes | yes |
| Calibrations, Chiller Log, Dev Tools, Git Work, Launcher, Screenshots, Shift planner, Time Converter | yes | no (none needed) |
| Pulser Monitor | **no** | yes |
| Extractor | no | no (not built on its own) |

Pulser Monitor is the one program that would benefit from an icon and does not have
one: both the Launcher card and the built exe use it.

---

## 2. The machines and shares

| Name | Path | Holds |
|------|------|-------|
| CPVA archiver | `https://10.78.0.57:8443/api/1.0/cpva` | every archived PV. `…/samples`, `…/channels`. **Self-signed certificate — verification is disabled everywhere.** |
| Camera image store | `\\users-L3.tier0.lcs.local\cpva-image-<year>` | the stored frames, `…/YYYY/M/D/H/<camera>/` |
| Scratch share (lab leg) | `\\hapls-share.lcs.local\scratch\Software` | the deployed programs, `Versions.txt`, `notes.txt` |
| Scratch share (office leg) | `\\hapls-share.cs.eli-beams.eu\scratch\Software` | the same NAS, reachable from the office network |
| SharePoint deploy target | `OneDrive - ELI Beamlines\L3-HAPLS\General\QoL` | second published copy |
| Build output | `…\programy\dist\<program>\vX.Y.Z\` | what Dev Tools produces |
| Salvation daily CSV | `\\hapls-share.cs.eli-beams.eu\scratch\Salvation\2026_alldata` (office: `Z:\Salvation\2026_alldata`) | the per-day energy CSV. **Dormant since 2026-08-19** — Salvation is not running, so no new file is written; Image Finder falls back to the archiver |
| Sources | `…\programy\L3-QoL-JanJan\` | this repository |

### The two share names — and the 48-second trap

The two scratch names resolve to **different IPs** (10.72.0.249 and 10.56.50.103;
assumed to be one NAS with a leg on each network). From an office PC only the
`cs.eli-beams.eu` name answers, in about 7 ms. The `lcs.local` name is unreachable
there — and a single `os.path.isdir()` on it **blocks for about 48 seconds** before
failing.

Consequences that any code touching a share must respect:

- Bound the wait and do it **off the UI thread**, or the app looks frozen at startup.
- Probing candidates concurrently is not enough: waiting for *all* probes still costs
  the full timeout. Decide as soon as the **highest-priority** candidate is known
  reachable, in a fixed order, so a machine that can reach both legs always picks the
  same file (`Diagnostic/shared_pvs.py` is the reference implementation).
- Abandoned probe threads must be plain **daemon** `threading.Thread`s.
  `ThreadPoolExecutor` workers are joined at interpreter exit and `QThreadPool` waits
  in its destructor, so either would move the 48 s stall to app *shutdown*.

The repository is inconsistent about which name is "lab": `Launcher/l.py` labels
`lcs.local` as Lab, `Image Tools/if_t.py` labels `cs.eli-beams.eu` as Lab. **Do not
guess from the hostname — probe.**

### Time: three clocks, and they disagree

| | Uses |
|---|---|
| Image-store folder names (`YYYY/M/D/H`) | **UTC**, and the numbers are unpadded |
| Filenames in the store | Unix **nanoseconds** (19 digits) |
| The archiver's sample timestamps | Unix nanoseconds |
| Everything shown to a user | **Europe/Prague**, DST included (`ZoneInfo`) |

Two measured facts that have caused real confusion:

- The archiver publishes roughly **1 s late**, and only about **one frame per 35 s**
  is stored.
- This office PC's clock has been observed running **~25 s ahead** of the facility.
  Never judge data freshness against the local clock.

### The archiver's limits

- A window of about **one hour** is the largest that answers reliably. Every program
  that asks for more splits the request into one-hour pieces and fetches them in
  parallel (`Chiller Log`, `CSS Logger`, `Image Tools`).
- A busy day can hold more samples than one request will return — it refuses somewhere
  above ~110 000. `Image Tools/cpva_client.py::fetch_samples_split` halves the window
  until each piece fits, so a search returns its shots instead of nothing. Slower,
  never incomplete.
- Nothing is recorded at night, so night hours are skipped rather than requested.
- **A frozen PV still returns fresh timestamps.** Only the repeating *value* gives it
  away — see Diagnostic's `detect_frozen`.

### The 16-bit image scale — the one shared gotcha

The store keeps `stored = raw_counts × 65535 / (2**bits − 1)`, where `bits` is **the
power-of-two bracket of that frame's own peak**, not the camera's bit depth. So
`value / 65535` is *not* a stable scale: a camera peaking near a bracket boundary
flips its factor between frames and its picture visibly doubles and halves in
brightness at unchanged shot energy.

`Image Tools/img_scale.py` is the single owner of that arithmetic. It recovers the raw
counts and renders every frame of a camera against the **largest bracket ever seen for
that camera** — learned, monotone, remembered in
`%APPDATA%\ELI_ImageTools\cam_depths.json`. All four Image Tools tabs load that one
module. Any new program showing stored frames must do the same, or its brightness will
flicker.

---

## 3. How a program reaches a lab PC

```
  Internal Builder                Dev Tools ▸ Builder            Dev Tools ▸ Copy Manager
  ────────────────                ───────────────────            ───────────────────────
  a file of imports          →    dist/<program>/vX.Y.Z/     →   <share>/<program>/
  built once                      <program> vX.Y.Z.exe            <program> vX.Y.Z.exe
        ↓                         + sources, icon, ReadMes        + sources, icon, ReadMes
   _internal/  ─────────────────────────────────────────────→     + images/ sounds/
   (the shared support folder,     + images/ sounds/ if any        + archive/vX.Y.Z/  (the
    ~hundreds of MB, moved                                          previous version)
    separately and rarely)                                              ↓
                                                                    Launcher
```

### The pieces

1. **The support folder.** A built program is a small starter exe plus an `_internal`
   folder holding the Python runtime and every library. That folder is large and
   almost identical for every program, so it is built **once** (Internal Builder) and
   the same copy is placed next to every program. Build it with its **`.spec` file**,
   never the bare command line: only the spec picks up numpy's (and scipy's) Windows
   DLLs, and a bundle missing them builds cleanly and fails days later inside a
   deployed program.

2. **The program.** Dev Tools ▸ Builder discovers projects (any folder with a source
   file), guesses the entry point, and builds folder-based and windowed. It excludes
   `test_*.py`, auto-collects numpy/scipy/sklearn/cv2/matplotlib/pandas when the
   sources import them, copies the sources, the icon, the `images`/`sounds`/`assets`/
   `icons` folders and the ReadMes into the version folder, and writes the new version
   into `<scratch>\Versions.txt`.

3. **Helper exes.** A project's `build_config.json` may list `extra_exes` — programs
   that live in its folder but are started on their own. Diagnostic's Webex listener
   is the only one. They build **single-file**, into their **own** dist entry with
   their own version, and so publish as their own program. A helper that fails to
   build only warns; the application is already built.

4. **Publishing.** Copy Manager moves the previous exe into `archive/vX.Y.Z/` first,
   then copies the new one. A locked exe (somebody is running it) is skipped, not
   failed. An icon change is shown side by side before it is overwritten. Leftover
   folders on the destination are removed — except `_internal`, `archive`, and the
   folders the new version itself brings.

5. **Running.** The Launcher scans a share (or the local `dist` tree), groups the
   programs, and starts them. It never installs anything.

6. **Rollback.** The Launcher can run an archived version. Because there is only one
   `_internal` per program, it does this by temporarily **swapping** the current exe
   aside, running the old one, and swapping back on exit. While that runs, anybody
   else launching from the same share gets the old version — so keep such runs short.
   A killed process leaves the swap unfinished; the Launcher then refuses that program
   and offers to repair it (also the 🧹 button).

### The two folder layouts the Launcher understands

```
deployed (a share)                build output (this PC)
<Program>/                        dist/<Program>/vX.Y.Z/
  <Program>.exe                     <Program> vX.Y.Z.exe
  _internal/                        _internal/
  archive/vX.Y.Z/…
```

### Version conventions

- `vX.Y.Z` folders and `Name vX.Y.Z.exe` filenames. The Launcher reads both.
- `archive/vX.Y.Z/` holds a **self-contained runnable snapshot**: the exe, the sources
  under their real importable names, and a log. No timestamps in names — the legacy
  flat form `Name vX.Y.Z__YYYYMMDD_HHMMSS.exe` is still accepted, and Copy Manager's
  **Fix** button converts it.
- `<scratch>\Versions.txt` is the flat `Name = vX.Y.Z` list everybody reads.

---

## 4. Where settings live, and why

Three homes, and the choice is never arbitrary.

| Home | Used for | Programs |
|------|----------|----------|
| `%APPDATA%\<Name>\` | per-user preferences and caches; anything that must survive a redeploy | `Launcher`, `DevTools`, `GitWork`, `PulserMonitor` (the hand-tuned ROI boxes!), `ELI_ImageTools`, `ELI_Spectra`, `Diagnostic` (run status) |
| Next to the program | per-installation configuration and data | `Screenshots/custom_presets.json`, `Announcer/presets.json`, `CSS Logger/*.json`, `Chiller Log/chiller_archive.csv`, `Diagnostic/monitor_config.json` |
| The scratch share | configuration that must be the same everywhere | `Diagnostic\monitor_pvs_shared.json` (the PV list, thresholds and monitoring settings) |
| Inside the build | credentials that cannot be per-PC | `Diagnostic/notify_provision.dat` (Teams / SMTP / Webex) |

Rules worth restating:

- **Anything hand-tuned goes in `%APPDATA%`.** Pulser Monitor's ROI boxes are the
  clearest case: they are per-camera hand work that cannot be regenerated, and a
  redeploy replaces the program folder.
- **Credentials must not sit in a readable file next to the exe.** They are Windows
  DPAPI blobs, tied to one account, so a local copy is worthless to anybody else
  anyway. Diagnostic therefore bakes the channel settings into the build as an
  obfuscated blob and strips them on save. Re-bake and rebuild after any change:
  `python notify_provision.py bake`.
- **`win32crypt` must be in every Diagnostic build.** It is imported lazily, so
  PyInstaller does not see it; `build_config.json` forces it. Without it a stored
  secret silently decrypts to an empty string and alerting stops with no error.
- **A shared config needs an offline fallback and a write guard.** Diagnostic keeps a
  full local mirror, starts from it when the share is unreachable, and then refuses to
  publish for that session — so a stale copy can never overwrite the group's.

---

## 5. Which program answers which question

Several programs read the same archive, and the difference is the time scale.

| Question | Program |
|----------|---------|
| What is the value right now, and warn me | **Diagnostic** ▸ PV Monitor |
| What did these values do over the last hours or days | **CSS Logger** |
| What is the chiller trend over months and years | **Chiller Log** |
| Which images exist, and what was the machine doing | **Image Tools** ▸ Image Finder |
| Play a sequence of frames, recorded or live | **Image Tools** ▸ Image Slider |
| Find the shots where a value was X | **Image Tools** ▸ Shot Finder |
| Measure and mark up one image | **Image Tools** ▸ Workshop |
| Collect a set of frames for the shift log | **Screenshots** |
| Make archive filenames readable | **Time Converter** |
| Analyse the spectrometer | **CSS Logger** ▸ Spectra |
| What did the pulsers do today | **Pulser Monitor** |
| Tell me when the screen changes | **Announcer** |

Deliberate overlaps, so nobody merges them by mistake:

- **Diagnostic vs CSS Logger vs Chiller Log** are live / short-window / long-term.
  Different sampling, different cost, different questions.
- **Announcer's PV badges vs Diagnostic's alerting.** Announcer's are a convenience
  beside its screen watch and only run while it is watching. Diagnostic is the real
  alerting.
- **Screenshots vs Image Tools.** Screenshots collects; Image Tools inspects.
- **Extractor vs Dev Tools ▸ Deploy internal.** Same job; Extractor is the manual
  fallback and its folder list is out of date.

---

## 6. Documentation convention

Every program folder carries **three** documents:

| File | Audience | Opened by |
|------|----------|-----------|
| `ReadMe_<folder>.txt` | anybody using the program: what it does, the principles, the main controls | the Launcher card's **ReadMe** button |
| `ReadMe_<folder>_Full.txt` | somebody who needs every control, setting, file format and failure mode | the Launcher card's **Details** button |
| `STRUCTURE.md` | whoever changes the code: modules, key functions, and *why* each decision is the way it is | read in the repo |

**The naming rule is not cosmetic.** `Launcher/l.py::find_readme_or_none` builds the
filename it looks for out of the **folder name** (`_norm("readme_" + folder)`), so:

- case, spaces, underscores, hyphens, dots and the extension are all ignored —
  `Readme Image Tools.txt` and `ReadMe_Image Tools.txt` both work;
- a plain `README.txt` matches **nothing** and the button silently opens nothing;
- `find_readme_full_or_none` accepts `ReadMe_<folder>_Full`,
  `ReadMe_<folder>_Details` or `Manual_<folder>`.

Dev Tools' deploy is more forgiving (it falls back to `README.txt`), so **a publish
can succeed while the Launcher still shows nothing**. That mismatch is easy to miss.

`cm_t.py::find_readme_full_or_none` mirrors the Launcher's matcher exactly, and both
the Builder and the Copy Manager carry **both** documents — the build puts them in the
version folder, `Copy` and `ReadMe only` publish them. If those two matchers ever
diverge, a publish lands a file the Launcher will not open, and nothing says so until
somebody presses the button.

### Documentation only reaches a share when it is published

Writing or editing a document in the repository changes nothing on `Z:`. Three
separate things have to happen before a lab PC sees a **Details** button:

1. the detailed file exists in the program's source folder;
2. it has been published — Dev Tools ▸ Copy Manager ▸ `Copy`, or `ReadMe only` when
   you do not want to rebuild the program;
3. the **Launcher on that share** is a build new enough to have the button. An older
   Launcher exe has no Details button in its code, however many files are published
   next to it.

Step 3 is the one that surprises people: the Launcher is itself just another deployed
program, so a change to it needs a build and a publish like any other.

`.txt` is used for both user documents so `os.startfile` always has a handler.
`STRUCTURE.md` stays Markdown because it is read in the repository, not launched.

### What belongs in which

- The short ReadMe explains *behaviour and principles* in plain language, using the
  labels that appear on screen. No code identifiers, no file paths unless the user
  needs them.
- The detailed ReadMe is the manual: every control, every setting with its default,
  the file formats, the numbers behind the thresholds, and a troubleshooting section.
- `STRUCTURE.md` records the *reasons*: the measurement that set a constant, the bug a
  guard exists to prevent, the ordering that must not change. A change log belongs in
  git; a "why this is not the obvious implementation" belongs here.

---

## 7. Shared code and conventions

There is no shared library — each program is self-contained on purpose, so one can be
rebuilt without touching the others.

**A module has exactly one home: the folder of the program that ships it.** Never keep
a second copy of a `.py` elsewhere and never add another folder to `sys.path` to reach
one. The builder passes only the program's own folder to PyInstaller and copies every
`.py` from it next to the exe, so a `sys.path` detour is followed when you run from
source and ignored in the build: the build silently uses the copy in the program
folder. `sp_t.py` lived like that (`Spectra/` on `sys.path`, a July copy in
`CSS Logger/`) and the built Spectra tab was five weeks behind the source for a month
without any error message. Need the same code in two programs? Copy it deliberately
and write it in the table below, or keep one program the owner and let the other stay
without it.

What is duplicated deliberately:

| Pattern | Appears in | Note |
|---------|-----------|------|
| `set_app_icon(win, ico, app_id)` | every tkinter program | tkinter's `iconbitmap` only sets the title bar; the Windows 11 taskbar reads the small-icon slots and the window-class icon. Must be **frozen-aware** — in a build `__file__` does not point next to the exe. |
| `get_app_dir()` / `_app_dir()` | every program | exe folder when frozen, source folder otherwise. Every path resolves through it. |
| Hour-chunked archiver fetch | Chiller Log, CSS Logger, Image Tools | the one-hour limit from §2 |
| The house calendar | CSS Logger (both tabs), Pulser Monitor, Chiller Log, Calibrations | Monday-first, grey header, red weekends, white cells. Weekends must be detected from the cell's **date**, never its column index. |
| Checkbox styling | the Qt programs | QSS on `::indicator` only — never a border on `QCheckBox {}` |
| Matplotlib toolbar | Pulser Monitor (`_make_mpl_toolbar`) | build it so the icons are **not** tinted; under the dark palette a tinted toolbar goes invisible |
| PV-name search | Image Slider (reference), CSS Logger (both tabs) | words are tokens, AND-matched anywhere in the name, order honoured, ranked, camera channels last, empty query returns nothing |
| Identity-keyed colours | CSS Logger (Spectra tab), Chiller Log, Diagnostic | a colour that names an item comes from the **item**, never from its index in the list being drawn — otherwise a missing item silently recolours everything else |
| Precise Qt timers | Image Tools | a default `QTimer` at 33 ms fires at 21 Hz on Windows; animation and playback need `PreciseTimer` |
| Brightness vs contrast | Image Tools, Screenshots | brightness = additive offset, contrast = multiplicative gain. Never swapped, in code or in labels. An Auto checkbox parks its slider on the value it computed. |

---

## 8. Testing

No shared test runner. Each program keeps its own offline checks next to it, and they
are all headless and network-free unless stated:

```
Image Tools/testing/     test_day_split.py, test_pv_resilience.py,
                         test_scale_invariance.py (needs the lab),
                         bench_*.py — the viewer, the live path, the PV panel
Pulser Monitor/          test_pulser.py, test_gui.py, test_real.py, test_gate.py
                         (run all four before changing the analyser)
Diagnostic/              test_alerting.py, test_monitor_frozen.py,
                         test_bot_commands.py
CSS Logger/              test_smoke.py, test_live_pacing.py,
                         test_count_param.py (needs the archiver)
```

`test_*.py` is never bundled into a build — the Builder excludes it by prefix.

---

## 9. Known gaps

- **Pulser Monitor has no `icon.ico`.**
- **Diagnostic, Pulser Monitor, Shift planner and Extractor are unclassified** in the
  Launcher and therefore land in "External".
- **Diagnostic's History tab cannot run**: its input file
  (`MasterOperations.parquet`) is no longer produced by anything.
- **Extractor's folder list is out of date** — it names programs that no longer exist
  and misses every newer one. Use Dev Tools ▸ Copy Manager instead.
- **Chiller Log** is not set up for a build (no `build_config.json`), and its settings
  file is only written if it already exists, so the chosen display range is not
  remembered on a fresh installation.
- **Chiller Log's "delete row"** menu entry does nothing (it compares timestamps in
  two different formats).
- **The Salvation daily-energy CSV is dormant** — that source stopped on 2026-08-19
  and is the dead half of a two-source lookup, kept wired up on purpose.
- **The lab/office share naming is inconsistent** across programs (§2).
