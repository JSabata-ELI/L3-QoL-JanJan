# Extractor — STRUCTURE

> Verified against source: 2026-08-19 · `e.py` 137 L

## Files

| File | Description |
|------|-------------|
| `e.py` | Single file. A console script, no window. Run with `python e.py`. |

No icon, no `build_config.json`, no config file. It is not built into an exe — it
needs a console for its `y/N` prompt and its progress output.

---

## Purpose

Takes the one `.zip` sitting in `Z:\Software` and unpacks it as the `_internal`
folder of every program listed in `TARGET_DIRS`. That zip is the shared PyInstaller
runtime produced by **Internal Builder**, so this is how one set of libraries gets
pushed to every deployed program at once.

**Largely superseded.** Dev Tools' Copy Manager now has **Build internal** and
**Deploy internal**, which do the same job from the GUI, against the current program
list, and without hard-coded paths. Keep `e.py` as the manual fallback for when the
Copy Manager cannot be used.

---

## Constants

| Symbol | Value |
|--------|-------|
| `SOFTWARE_ROOT` | `Z:\Software` — hard-coded. Holds both the zip and the target folders. |
| `TARGET_DIRS` | The program folders to unpack into. |

`TARGET_DIRS` is **stale**: `Calibrations`, `Copy manager`, `Counter of shots`,
`Image Finder`, `Image Slider`, `Image Tools`, `Launcher`, `Screenshots`,
`Time converter`. Several of those programs no longer exist under those names
(Image Finder and Image Slider became tabs of Image Tools; Copy manager became a
tab of Dev Tools), and the newer programs — Announcer, CSS Logger, Diagnostic, Dev
Tools, Git Work, Pulser Monitor, Shift planner, Spectra, Chiller Log — are not in
the list at all. A folder that does not exist is reported as missing and skipped,
so the script does not fail; it just quietly does less than it looks like it does.

---

## Functions

| Function | Description |
|----------|-------------|
| `find_zip(root)` | The single `.zip` in the root. With more than one it warns, lists them all, and uses the first. |
| `extract_to(zip_path, dst_dir)` | Replace one program's `_internal`. Returns True/False. |
| `main()` | Check the root exists → find the zip → list which target folders were found and which are missing → ask `y/N` → unpack into each → print an OK/failed tally. |

### How `extract_to` works

1. **Delete the old `_internal` outright.** There is no backup, despite what the
   confirmation text says (see below).
2. Work out the path prefix to strip. A zip built by PyInstaller `--onedir` has its
   files under something like `_internal_builder/_internal/…`, so the script scans
   the entries for the first one containing `_internal/` and takes everything up to
   and including that as the prefix. Entries not starting with the prefix are
   skipped.
3. Extract the remainder into the program's `_internal`, printing progress every
   200 files.

**Known wording bug:** the confirmation prompt says *"Existing `_internal` folders
will be archived with a timestamp suffix"*, but step 1 deletes them. The
`timestamp` variable in `extract_to` is computed and never used — a leftover from
an archiving version that no longer exists. Either restore the archiving or fix the
message; as it stands the prompt promises a safety net that is not there.

---

## Behaviour notes

- Interactive by design: it needs a console for the `y/N` confirmation and shows
  its progress there.
- Matches the layout of a PyInstaller `--onedir` build. Result:
  `Z:\Software\<Program>\_internal\…`.
- Writes to the `Z:` share, so it must be run from a machine that can reach it.

---

## Dependencies

```
zipfile, shutil, pathlib, datetime   stdlib only
```
