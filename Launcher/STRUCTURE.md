# Launcher — STRUCTURE

## Soubory

| Soubor | Popis |
|--------|-------|
| `l.py` | Jediný soubor. Celý launcher v tkinter. |

---

## l.py

### Konstanty a konfigurace
- `CONFIG_PATH` — `%APPDATA%\Launcher\config.json` (ukládá nastavitelné cesty)
- `ROOT_OPTIONS` — seznam kořenových složek pro skenování programů:
  - `Lab - Scratch` → `\\hapls-share.lcs.local\scratch\Software`
  - `Office - Scratch` → z configu
  - `Office - Sharepoint` → z configu
  - `Office - Programs` → `~/OneDrive - ELI Beamlines/ELI Beamlines/Python/programy`
- `NOTES_LAB` / `NOTES_OFFICE` — cesty k poznámkovým souborům
- `IGNORE_DIR_NAMES` — složky ignorované při skenování (`archive`, `dist`)

### Skupiny programů
| Konstanta | Programy |
|-----------|----------|
| `SCRIPTS` | Image Tools, Screenshots, Time Converter, Dev Tools, Announcer, CSS Logger |
| `PARTS` | Image Finder, Image Slider, Shot finder, Launcher |
| `IN_PROGRESS` | Calibrations |
| `NOT_WORKING_CORRECTLY` | Counter of Shots |
| `PERSONAL` | Copy Manager, Builder, Internal Builder |
| *(vše ostatní)* | External |

Pořadí zobrazení skupin: Scripts → Parts → External → In progress → Not working correctly → Personal

### Funkce pro skenování
| Funkce | Popis |
|--------|-------|
| `scan_programs(root)` | Projde root složku, vrátí dict `{program_name: {...}}` |
| `_build_version_list(exes, program_dir)` | Sestaví seznam aktuálních + archivních verzí pro dropdown |
| `scan_archive_versions(program_dir)` | Vrátí archivní exe ze složky `archive/` |
| `newest_version_folder(dist_dir)` | Najde nejvyšší `vX.Y.Z` složku |
| `find_readme_or_none(program_dir, name)` | Robust hledání ReadMe souboru (case/separator-insensitive) |
| `pick_exe(exes, program_name, ver_folder)` | Vybere správné exe ze seznamu |
| `group_for_program(name)` | Vrátí klíč skupiny (`scripts`, `parts`, `personal`, ...) |

### Verze a naming
- `VERSION_RE` — `v(\d+)\.(\d+)\.(\d+)$` pro složky
- `ARCHIVE_EXE_RE` — `name v1.2.3__20250101_120000.exe` pro archivní exe
- `_exe_version(p)` — tuple verze z názvu exe
- `_archive_exe_label(p)` — čitelný label `"v1.2.3  (2025-01-01  12:00:00)"`

### Polling verzí
- Interval: **10 000 ms** (10 s)
- Pokud je dostupná novější verze, tlačítko programu začne vizuálně signalizovat update

### Ikony
- `find_icon_for_program(program_dir, exe)` — hledá `.ico` v adresáři programu
- `show_icon_compare_dialog(parent, name, existing, incoming)` — porovnání ikon při konfliktu

### UI (tkinter)
- Hlavní okno s záložkami pro každý root
- V každé záložce: collapsible skupiny (Scripts, Parts, ...) s tlačítky programů
- Každé tlačítko programu: název + verze + dropdown archivních verzí + ReadMe
- Pravý panel: poznámky (sdílený `.txt` soubor), log

### Konfigurace (config.json)
```json
{
  "office_scratch": "cesta...",
  "office_sharepoint": "cesta..."
}
```
