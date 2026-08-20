# Internal Builder — STRUCTURE

> Verified against source: 2026-08-19 · `_internal_builder.py` 169 L

User-facing documentation: `Readme Internal Builder.txt` (short, the Launcher's **ReadMe**
button) and `ReadMe_Internal Builder_Full.txt` (detailed, the Launcher's **Details** button).
Shared infrastructure — paths, the build/deploy chain, where settings live:
`../INFRASTRUCTURE.md`.

## Files

| File | Description |
|------|-------------|
| `_internal_builder.py` | A file of nothing but imports. **Never run as a program** — it exists to be built. |
| `_internal_builder.spec` | The PyInstaller spec, which is what an actual build should use. |
| `build_config.json` | Extras for the Dev Tools Builder. |
| `icon.ico` | Icon for the produced exe (which nobody launches). |
| `build/` | PyInstaller work directory left behind by a build. |

---

## Purpose

Every program in this repository uses the same set of Python libraries. Rather than
each build carrying its own copy, this one dummy project is built with PyInstaller
so that its `_internal/` folder holds all of them, and that single folder is then
distributed to each deployed program.

The distribution end is **Dev Tools → Copy Manager → Build internal / Deploy
internal**; `Extractor/e.py` is the older manual route.

The file **is not executed as an application.** Its whole content is import
statements, and that is the point: PyInstaller follows imports, so an import here
is a library in the resulting `_internal/`. To add a library to the shared bundle,
import it here.

---

## What it imports

| Group | Modules |
|-------|---------|
| stdlib | `argparse`, `atexit`, `bisect`, `collections`, `concurrent.futures`, `configparser`, `contextlib`, `csv`, `ctypes` (+ `wintypes`), `dataclasses`, `datetime`, `json`, `math`, `os`, `pathlib`, `re`, `shutil`, `socket`, `subprocess`, `sys`, `tempfile`, `threading`, `time`, `traceback`, `typing`, `ssl`, `urllib.parse`, `urllib.request`, `webbrowser`, `zipfile`, `zoneinfo` |
| tkinter | the full set — `ttk`, `messagebox`, `filedialog`, `simpledialog`, `scrolledtext`, `tkinter.font`, and the widget names used directly |
| Qt | `PySide6` — QtCore, QtGui, QtWidgets |
| scientific | `numpy`, `pandas`, `scipy`, `scipy.ndimage` |
| plotting | `matplotlib` — the Agg and Qt backends, `Figure`, `GridSpec`, … |
| imaging | `PIL` / Pillow, including `ImageTk`, `ImageGrab` and the `PIL._imaging` C extension |
| other | `screeninfo`, `orjson`, `requests`, `dateutil` |
| optional, in `try/except ImportError` | `xlwt`, `tkcalendar`, `epics`, `win32com.client` + `pythoncom` |

The optional ones are wrapped so a machine without them can still produce a bundle
— just one missing that library.

---

## Two ways to build it, and they are not the same

### The `__main__` block

Running the file starts a build of itself:

```
py -m PyInstaller --onedir --windowed --name "_internal_builder"
   --collect-all PIL --collect-all matplotlib --collect-all pyparsing
   --collect-all cycler --collect-all kiwisolver --collect-all contourpy
   --collect-all fonttools --collect-all packaging --collect-all python-dateutil
   --collect-all screeninfo --collect-all scipy --collect-all numpy
   --collect-all orjson --collect-data certifi
   --hidden-import zoneinfo._tzdata
   --distpath "C:\Dev\dist" --noconfirm _internal_builder.py
```

Note `--distpath "C:\Dev\dist"` — hard-coded.

### The spec file

`_internal_builder.spec` does the same `collect_all` calls, **plus two things the
command line cannot do:**

- **numpy 2.x on Windows keeps its BLAS DLLs in a sibling `numpy.libs/` package**
  (put there by delvewheel), which `collect_all('numpy')` does not see. The spec
  globs those DLLs in explicitly, so the shared `_internal` stays self-consistent —
  the `.pyd` files and the DLLs they load must come from the same build.
- The same treatment for `scipy.libs/` when it is present.

**A bundle built from the command line alone can therefore be missing numpy's BLAS
DLLs**, which shows up much later as an import error inside a deployed program, not
as a build failure. Prefer the spec.

`build_config.json` adds `collect_all: ["PIL"]` and forces the `PIL` submodules
(`Image`, `ImageGrab`, `_imaging`, `ImageDraw`, `ImageFont`, `ImageTk`) as hidden
imports, for the path where Dev Tools drives the build.

---

## Dependencies

Everything listed under *What it imports*. The build machine needs each of them
installed, or the bundle simply will not contain it.
