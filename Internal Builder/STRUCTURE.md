# Internal Builder — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `_internal_builder.py` | Dummy skript — buildí se přes PyInstaller, ale nespouští se. |
| `_internal_builder.spec` | PyInstaller spec soubor pro build. |
| `build_config.json` | Konfigurace pro Dev Tools Builder (název projektu, main file). |

---

## _internal_builder.py

### Záměr
Tento soubor se **nespouští** — slouží pouze jako target pro PyInstaller build. Výsledkem buildu je `_internal/` složka se sdílenými závislostmi pro všechny programy v Image Tools suite (PIL, matplotlib, scipy, PySide6, numpy, pandas, …).

Tato sdílená `_internal/` se pak distribuuje pomocí nástroje **Extractor** (`e.py`) do složky každého programu na `Z:\Software`.

### Obsah
Pouze importy potřebných knihoven — žádná aplikační logika. PyInstaller tyto importy zabalí do `_internal/`:

- **stdlib:** `argparse`, `csv`, `ctypes`, `json`, `pathlib`, `ssl`, `threading`, `zipfile`, `zoneinfo`, …
- **tkinter** — kompletní sada (`ttk`, `messagebox`, `filedialog`, …)
- **PySide6** — Core, Gui, Widgets (kompletní sada Qt widgetů)
- **vědecké:** `numpy`, `pandas`, `scipy`, `scipy.ndimage`
- **vizualizace:** `matplotlib` (Agg + Qt backend, Figure, GridSpec, …)
- **obraz:** `PIL` / Pillow + `ImageTk`, `ImageGrab`, `PIL._imaging` (C extension)
- **ostatní:** `screeninfo`, `orjson`, `requests`, `dateutil`, `xlwt` (opt.), `epics` (opt.), `win32com` (opt.)

### Build příkaz
Uložen přímo v `__main__` bloku souboru — spuštění souboru zahájí build:
```
py -m PyInstaller --onedir --windowed --name "_internal_builder"
    --collect-all PIL --collect-all matplotlib --collect-all scipy
    --collect-all screeninfo ...
    --distpath "C:\Dev\dist" --noconfirm _internal_builder.py
```

### Závislosti (volitelné / try-except)
`PySide6`, `numpy`, `pandas`, `scipy`, `matplotlib`, `PIL`, `screeninfo`, `orjson`, `requests`, `dateutil`, `xlwt`, `tkcalendar`, `epics`, `win32com`
